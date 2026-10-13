#!/usr/bin/env python3
"""Play footage from a HIKVISION DVR disk or image without extracting it first.

Usage:
    python hikextractor-viewer.py [INPUT_IMAGE.DD]

Needs PyQt6 and FFmpeg 5.1 or later, the same as hikextractor-gui.py.
"""
import queue
import shutil
import subprocess
import sys
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from hikvision_parser import HIKBTREEEntry, HikvisionParser

VIDEO_WIDTH, VIDEO_HEIGHT = 800, 450
FRAME_SIZE = VIDEO_WIDTH * VIDEO_HEIGHT * 3  # one RGB24 frame from FFmpeg

PS_PACK_HEADER = b"\x00\x00\x01\xba"
# H.264 SPS and H.265 VPS NAL units, which the DVR writes before every keyframe
KEYFRAME_MARKERS = (b"\x00\x00\x01\x67", b"\x00\x00\x01\x40")
KEYFRAME_SEARCH_SIZE = 8 * 1024 * 1024
INDEX_SIZE = 0x100000  # the last 1 MiB of each data block holds an index, not video
READ_CHUNK = 1024 * 1024

FRAME_INTERVAL_MS = 40       # 25 fps, the usual rate for PAL CCTV
FAST_SCAN_INTERVAL_MS = 100
SLIDER_STEPS = 1000
FAST_SCAN = "Fast scan (keyframes only)"
SPEEDS = {"1x": 1, "2x": 2, "4x": 4, "8x": 8, FAST_SCAN: 1}  # frames shown per tick


def read_at(path: str, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


class Viewer(QWidget):
    def __init__(self, source_path: str):
        super().__init__()
        self.source_path = source_path
        master, entries = HikvisionParser(source_path).parse_metadata()
        self.video_size = master.size_data_block - INDEX_SIZE

        self.blocks_by_channel = {}
        for entry in sorted((e for e in entries if not e.recording), key=lambda e: e.start_timestamp):
            self.blocks_by_channel.setdefault(entry.channel, []).append(entry)
        self.channels = sorted(self.blocks_by_channel)
        self.channel_blocks = []
        self.block: Optional[HIKBTREEEntry] = None

        self.process: Optional[subprocess.Popen] = None
        self.frames: queue.Queue = queue.Queue()
        self.frames_per_tick = 1
        # Bumped every time playback stops, so worker threads from the old run exit
        self.playback_id = 0
        # Byte offset FFmpeg has been fed up to; drives the slider and time label
        self.feed_position = 0

        self.build_ui()

    def build_ui(self):
        self.channel_list = QListWidget()
        for channel in self.channels:
            count = len(self.blocks_by_channel[channel])
            self.channel_list.addItem(f"Channel {channel:02d}  ({count} {'block' if count == 1 else 'blocks'})")
        self.block_list = QListWidget()
        for list_widget in (self.channel_list, self.block_list):
            list_widget.setFixedWidth(250)

        self.video = QLabel("Select a channel, then a block")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setFixedSize(VIDEO_WIDTH, VIDEO_HEIGHT)
        self.video.setStyleSheet("background: black; color: white")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMaximum(SLIDER_STEPS)
        self.slider.setTracking(False)  # seek on release, not on every pixel of a drag
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.speed = QComboBox()
        self.speed.addItems(SPEEDS)
        self.time_label = QLabel()
        self.timer = QTimer()

        left = QVBoxLayout()
        left.addWidget(QLabel("Channels"))
        left.addWidget(self.channel_list)
        left.addWidget(QLabel("Blocks"))
        left.addWidget(self.block_list)
        controls = QHBoxLayout()
        controls.addWidget(self.pause_button)
        controls.addWidget(self.speed)
        controls.addWidget(self.time_label, 1)
        right = QVBoxLayout()
        right.addWidget(self.video)
        right.addWidget(self.slider)
        right.addLayout(controls)
        right.addStretch()
        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addLayout(right)

        self.channel_list.currentRowChanged.connect(self.show_channel)
        self.block_list.currentRowChanged.connect(self.select_block)
        self.slider.valueChanged.connect(self.seek)
        self.speed.currentIndexChanged.connect(self.restart)
        self.pause_button.toggled.connect(self.set_paused)
        self.timer.timeout.connect(self.show_next_frame)

    def show_channel(self, row: int):
        self.stop()
        self.block = None
        self.block_list.clear()
        self.channel_blocks = self.blocks_by_channel[self.channels[row]]
        for entry in self.channel_blocks:
            self.block_list.addItem(f"{entry.start_timestamp:%Y-%m-%d %H:%M} to {entry.end_timestamp:%Y-%m-%d %H:%M}")

    def select_block(self, row: int):
        if row < 0:  # the list was cleared
            return
        self.block = self.channel_blocks[row]
        self.set_slider(0)
        self.play(0)

    def seek(self, value: int):
        self.play(value / SLIDER_STEPS)

    def restart(self):
        self.play(self.slider.value() / SLIDER_STEPS)

    def set_paused(self, paused: bool):
        # While paused the frame queue fills up and FFmpeg blocks, so nothing more is read
        if paused:
            self.timer.stop()
        elif self.process:
            self.timer.start()

    def play(self, fraction: float):
        if self.block is None:
            return
        self.stop()
        block_start = self.block.offset_datablock
        block_end = block_start + self.video_size
        is_program_stream = PS_PACK_HEADER in read_at(self.source_path, block_start, 4096)
        start = block_start + int(self.video_size * fraction)
        if fraction > 0:
            start = self.next_keyframe(start, block_end, is_program_stream)

        keyframes_only = self.speed.currentText() == FAST_SCAN
        self.process = subprocess.Popen(
            self.ffmpeg_command(is_program_stream, keyframes_only),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        self.frames = queue.Queue(maxsize=25)
        self.frames_per_tick = SPEEDS[self.speed.currentText()]
        self.feed_position = start
        threading.Thread(target=self.feed_ffmpeg, args=(self.process, start, block_end, self.playback_id),
                         daemon=True).start()
        threading.Thread(target=self.read_frames, args=(self.process, self.frames, self.playback_id),
                         daemon=True).start()
        self.timer.setInterval(FAST_SCAN_INTERVAL_MS if keyframes_only else FRAME_INTERVAL_MS)
        if not self.pause_button.isChecked():
            self.timer.start()

    def next_keyframe(self, offset: int, block_end: int, is_program_stream: bool) -> int:
        """Return the offset of the first keyframe at or after offset, so decoding starts cleanly."""
        window = read_at(self.source_path, offset, min(KEYFRAME_SEARCH_SIZE, block_end - offset))
        hits = [i for i in (window.find(marker) for marker in KEYFRAME_MARKERS) if i >= 0]
        if not hits:
            return offset
        keyframe = min(hits)
        if is_program_stream:
            # Start at the PS pack that carries the keyframe, not in the middle of it
            pack = window.rfind(PS_PACK_HEADER, 0, keyframe)
            if pack >= 0:
                keyframe = pack
        return offset + keyframe

    @staticmethod
    def ffmpeg_command(is_program_stream: bool, keyframes_only: bool) -> list:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-fflags", "+discardcorrupt"]
        if keyframes_only:
            command += ["-skip_frame", "nokey"]
        command += [
            "-f", "mpeg" if is_program_stream else "h264", "-i", "pipe:0",
            "-map", "0:v:0", "-an",
            "-vf", f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
                   f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:-1:-1",
            "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ]
        return command

    def feed_ffmpeg(self, process: subprocess.Popen, position: int, end: int, playback_id: int):
        """Worker thread: copy the block from the disk to FFmpeg's stdin."""
        try:
            with open(self.source_path, "rb") as source:
                source.seek(position)
                while position < end and playback_id == self.playback_id:
                    chunk = source.read(min(READ_CHUNK, end - position))
                    process.stdin.write(chunk)
                    position += len(chunk)
                    if playback_id == self.playback_id:
                        self.feed_position = position
            process.stdin.close()
        except (OSError, ValueError) as e:
            # After a stop this is just the closed pipe; otherwise it's a real disk or FFmpeg error
            if playback_id == self.playback_id:
                print(f"Playback stopped at offset {position}: {e}", file=sys.stderr)

    def read_frames(self, process: subprocess.Popen, frames: queue.Queue, playback_id: int):
        """Worker thread: queue decoded frames, then None at the end of the block."""
        while playback_id == self.playback_id:
            frame = process.stdout.read(FRAME_SIZE)
            if len(frame) < FRAME_SIZE:
                frame = None
            while playback_id == self.playback_id:
                try:
                    frames.put(frame, timeout=0.2)
                    break
                except queue.Full:
                    pass
            if frame is None:
                return

    def show_next_frame(self):
        frame = None
        for _ in range(self.frames_per_tick):  # at 4x, show every fourth frame
            try:
                next_frame = self.frames.get_nowait()
            except queue.Empty:
                break
            if next_frame is None:
                self.play_next_block()
                return
            frame = next_frame
        if frame:
            image = QImage(frame, VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_WIDTH * 3, QImage.Format.Format_RGB888)
            self.video.setPixmap(QPixmap.fromImage(image))
        self.update_position()

    def play_next_block(self):
        next_row = self.block_list.currentRow() + 1
        if next_row < self.block_list.count():
            self.block_list.setCurrentRow(next_row)
        else:
            self.stop()

    def update_position(self):
        block = self.block
        fraction = (self.feed_position - block.offset_datablock) / self.video_size
        fraction = min(max(fraction, 0.0), 1.0)
        if not self.slider.isSliderDown():
            self.set_slider(int(fraction * SLIDER_STEPS))
        # Footage isn't spread evenly through a block, so this is only an estimate
        approx = block.start_timestamp + (block.end_timestamp - block.start_timestamp) * fraction
        self.time_label.setText(f"approx. {approx:%Y-%m-%d %H:%M}")

    def set_slider(self, value: int):
        self.slider.blockSignals(True)  # moving it ourselves shouldn't trigger a seek
        self.slider.setValue(value)
        self.slider.blockSignals(False)

    def stop(self):
        self.timer.stop()
        self.playback_id += 1
        if self.process:
            self.process.kill()
            self.process.wait()
            self.process = None

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    if shutil.which("ffmpeg") is None:
        QMessageBox.critical(None, "FFmpeg not found", "FFmpeg must be in the search path to play footage.")
        return 2
    if len(sys.argv) > 1:
        source_path = sys.argv[1]
    else:
        source_path, _ = QFileDialog.getOpenFileName(None, "Select HD raw image")
        if not source_path:
            return 0
    try:
        viewer = Viewer(source_path)
    except Exception as e:
        QMessageBox.critical(None, "Could not read the image", str(e))
        return 1
    viewer.setWindowTitle(f"HIKVISION viewer - {source_path}")
    viewer.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
