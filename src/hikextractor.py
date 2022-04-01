import mmap
import struct
import subprocess
import sys
import os.path
import argparse
import dataclasses
from datetime import datetime
from typing import List, Optional

SIGNATURE = b"HIKVISION@HANGZHOU"
IDR_ENTRY_SIGNATURE = b"OFNI"
HIKBTREE_SIGNATURE = b"HIKBTREE"
BA_NAL = bytes.fromhex("00 00 01 BA")


# Data model
@dataclasses.dataclass(frozen=True)
class MasterBlock:
    signature: bytes
    version: bytes
    capacity: int
    offset_system_logs: int
    size_system_logs: int
    offset_video_area: int
    size_data_block: int
    total_data_blocks: int
    offset_hibtree1: int
    size_hibtree1: int
    offset_hibtree2: int
    size_hibtree2: int
    time_system_init: datetime


@dataclasses.dataclass(frozen=True)
class IDREntry:
    index: int
    timestamp: datetime
    offset_next_entry: int
    num1: int
    num2: int
    num3: int
    num4: int


@dataclasses.dataclass(frozen=True)
class HIKBTREEEntry:
    channel: int
    has_footage: bool
    recording: bool
    start_timestamp: Optional[datetime]
    end_timestamp: Optional[datetime]
    offset_datablock: int


@dataclasses.dataclass(frozen=True)
class IDRHeaderPage:
    channel: int
    start_timestamp: datetime
    end_timestamp: datetime
    offset_idr_1: int
    offset_idr_2: int


@dataclasses.dataclass(frozen=True)
class IDRHeader:
    channel: int
    offset_to_datablock_start: int
    start_timestamp: datetime
    end_timestamp: datetime
    pages: List


# Helper functions


def to_uint8(buff: bytes, offset: int) -> int:
    return struct.unpack("B", buff[offset : offset + 1])[0]


def to_uint32(buff: bytes, offset: int) -> int:
    return struct.unpack("<I", buff[offset : offset + 4])[0]


def to_uint64(buff: bytes, offset: int) -> int:
    return struct.unpack("<Q", buff[offset : offset + 8])[0]


def to_datetime(buff: bytes, offset: int) -> datetime:
    return datetime.utcfromtimestamp(to_uint32(buff, offset))


def check_all_zeros(buff: bytes) -> bool:
    for b in buff:
        if b != 0:
            return False
    return True


def find_in_bytes(buff: bytes, what: bytes, start, size=1024 * 1024):
    result = buff[start : start + size].find(what)
    if result < 0:
        return result
    return result + start


# Parsing functions


def parse_master_block(mappedfile) -> MasterBlock:
    master = mappedfile[0x200:0x360]
    signature = bytes(master[0x10:0x22])
    if signature != SIGNATURE:
        raise Exception("Wrong master block signature")
    version = bytes(master[0x30:0x3E])
    capacity = to_uint64(master, 0x48)
    offset_system_logs = to_uint64(master, 0x60)
    size_system_logs = to_uint64(master, 0x68)
    offset_video_area = to_uint64(master, 0x78)
    size_data_block = to_uint64(master, 0x88)
    total_data_blocks = to_uint32(master, 0x90)
    offset_hibtree1 = to_uint64(master, 0x98)
    size_hibtree1 = to_uint32(master, 0xA0)
    offset_hibtree2 = to_uint64(master, 0xA8)
    size_hibtree2 = to_uint32(master, 0xB0)
    time_system_init = to_datetime(master, 0xF0)
    return MasterBlock(
        signature=signature,
        capacity=capacity,
        version=version,
        offset_system_logs=offset_system_logs,
        size_system_logs=size_system_logs,
        offset_video_area=offset_video_area,
        size_data_block=size_data_block,
        total_data_blocks=total_data_blocks,
        offset_hibtree1=offset_hibtree1,
        size_hibtree1=size_hibtree1,
        offset_hibtree2=offset_hibtree2,
        size_hibtree2=size_hibtree2,
        time_system_init=time_system_init,
    )


def parse_hbt_entry(data, offset) -> Optional[HIKBTREEEntry]:
    has_footage = to_uint64(data, offset + 0x8) == 0
    channel = to_uint8(data, offset + 0x11)
    dt1 = to_uint32(data, offset + 0x18)
    offset_datablock = to_uint64(data, offset + 0x20)
    recording = False
    start_timestamp = None
    end_timestamp = None
    if has_footage:
        if dt1 == 0x7FFFFFFF:
            recording = True
        else:
            start_timestamp = to_datetime(data, offset + 0x18)
            end_timestamp = to_datetime(data, offset + 0x1C)
    else:
        return None
    return HIKBTREEEntry(
        channel=channel,
        has_footage=has_footage,
        recording=recording,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        offset_datablock=offset_datablock,
    )


def parse_hbtree(data, masterblock: MasterBlock) -> List[HIKBTREEEntry]:
    offset = masterblock.offset_hibtree1
    signature = bytes(data[offset + 0x10 : offset + 0x18])
    if signature != HIKBTREE_SIGNATURE:
        raise Exception("Wrong HIKBTREE Signature")
    offset_page_list = to_uint64(data, offset + 0x50)

    # parse page list:
    offset_page = to_uint64(data, offset_page_list + 0x18)

    entries = []
    safe_count = 0

    # parse pages:
    while True:
        entry_count = to_uint32(data, offset_page + 0x10)
        next_page = to_uint64(data, offset_page + 0x20)
        first_entry = offset_page + 0x60
        for i in range(entry_count):
            entry = parse_hbt_entry(data, first_entry + i * 48)
            if entry is not None:
                entries.append(entry)
        if next_page == 0xFFFFFFFFFFFFFFFF:
            break
        offset_page = next_page
        safe_count += 1
        if safe_count > 100:
            break

    return entries


# IDR Parsing still not functional


def parse_idr_entry(datablock, offset):
    signature = bytes(datablock[offset : offset + 4])
    if signature != IDR_ENTRY_SIGNATURE:
        raise Exception("Wrong IDR Entry Signature")

    # entry_size = to_uint8(datablock, offset + 4)
    offset_next_entry = to_uint32(datablock, offset + 0x14)
    timestamp = to_datetime(datablock, offset + 0x18)
    num1 = to_uint32(datablock, offset + 0xC)
    num2 = to_uint32(datablock, offset + 0x20)
    num3 = to_uint32(datablock, offset + 0x24)
    num4 = to_uint32(datablock, offset + 0x28)
    return IDREntry(
        index=0,
        timestamp=timestamp,
        offset_next_entry=offset_next_entry,
        num1=num1,
        num2=num2,
        num3=num3,
        num4=num4,
    )


def parse_idr_header_page(page: bytes, offset_datablock):
    channel = to_uint8(page, 0xD)
    start_timestamp = to_datetime(page, 0x28)
    end_timestamp = to_datetime(page, 0x30)
    offset_idr_1 = to_uint32(page, 0x6C) + offset_datablock
    offset_idr_2 = to_uint32(page, 0x70) + offset_datablock
    return IDRHeaderPage(
        channel=channel,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        offset_idr_1=offset_idr_1,
        offset_idr_2=offset_idr_2,
    )


def parse_idr_header(datablock, masterblock: MasterBlock):
    offset = masterblock.size_data_block - 0x100000
    data = datablock[offset : offset + 0x200]
    channel = to_uint8(data, 0xD)
    offset_to_datablock_start = to_uint64(data, 0x18)
    start_timestamp = to_datetime(data, 0x20)
    end_timestamp = to_datetime(data, 0x24)
    pages = []
    while True:
        offset += 0x200
        page = datablock[offset : offset + 0x200]
        if check_all_zeros(page):
            break
        pages.append(parse_idr_header_page(page, offset_to_datablock_start))
    return IDRHeader(
        channel=channel,
        offset_to_datablock_start=offset_to_datablock_start,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        pages=pages,
    )


def read_idr_table(datablock):
    pass


# Export functions


def export_footage_from_block(datablock, outfile):
    start_offset = find_in_bytes(datablock, BA_NAL, 0, 4096)
    if start_offset < 0:
        return
    while True:
        end_offset = find_in_bytes(datablock, BA_NAL, start_offset + 5, 120 * 1024)
        if end_offset < 0:
            return
        outfile.write(datablock[start_offset:end_offset])
        start_offset = find_in_bytes(datablock, BA_NAL, end_offset, 512)
        if start_offset < 0:
            return


def export_file(datablock, filename):
    with subprocess.Popen(
        [
            "ffmpeg",
            "-i",
            "-",
            "-c:v",
            "copy",
            "-bsf:v",
            "filter_units=pass_types=1-5",
            "-aspect",
            "4/3",
            "-loglevel",
            "error",
            "-stats",
            filename,
        ],
        stdin=subprocess.PIPE,
    ) as ffmpeg:
        export_footage_from_block(datablock, ffmpeg.stdin)
        ffmpeg.communicate()


def export_all_videos(source, dest_folder, list_only=False, master_only=False):
    with open(source, "rb") as input_image, mmap.mmap(input_image.fileno(), 0, access=mmap.ACCESS_READ) as mmapped_file:
        try:
            master = parse_master_block(mmapped_file)
            print(f"HD Signature: {master.signature.decode('utf-8')}")
            print(f"Filesystem version: {str(master.version.decode('utf-8'))}")
            print(f"HD Capacity: {master.capacity} bytes")
            print(f"Data block size: {master.size_data_block} bytes")
            print(f"Time System Init: {master.time_system_init:%Y-%m-%d %H:%M}")
            print()
            if master.version != b"HIK.2011.03.08":
                print(
                    "This script was tested only with version HIK.2011.03.08"
                    " of filesystem, but version "
                    f"{master.version.decode('utf-8')}"
                    " was found. Use at your own risk."
                )
                print()
            entrylist = parse_hbtree(mmapped_file, master)
        except Exception as e:
            print(e, file=sys.stderr)
            exit(1)

        if master_only:
            return

        channels = dict()
        for entry in entrylist:
            if entry.channel not in channels.keys():
                channels[entry.channel] = 0
            channels[entry.channel] += 1

        sorted_channels = sorted(list(channels.keys()))
        for ch in sorted_channels:
            print(f"Channel {ch:02d}: {channels[ch]} video blocks")

        # sort by start datetime and channel
        def sortkey(x):
            if x.recording:
                return f"00REC-{x.channel:02d}"
            return f"{x.start_timestamp:%Y%m%d%H%M}-{x.channel:02d}"

        entrylist = sorted(entrylist, key=sortkey)

        for entry in entrylist:
            if entry.recording:
                filename = f"CH-{entry.channel:02d}__RECORDING.mp4"
            else:
                start = entry.start_timestamp
                end = entry.end_timestamp
                filename = f"CH-{entry.channel:02d}__{start:%Y-%m-%d-%H-%M}__{end:%Y-%m-%d-%H-%M}.mp4"
            start_offset = entry.offset_datablock
            end_offset = start_offset + master.size_data_block
            if list_only:
                if entry.recording:
                    print(f"Channel {entry.channel:02d}, block being recorded.")
                else:
                    print(f"Channel {entry.channel:02d}, from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}")
            else:
                print()
                if entry.recording:
                    print(f"Exporting footage for channel {entry.channel:02d}, block being recorded.")
                else:
                    print(
                        f"Exporting footage for channel {entry.channel:02d}, "
                        f"from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}"
                    )
                export_file(
                    mmapped_file[start_offset:end_offset],
                    filename=os.path.join(dest_folder, filename),
                )


# Main

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        dest="input",
        required=True,
        help="Raw image file from the DVR HD",
    )
    parser.add_argument("-o", "--output", dest="output", required=False, default=None, help="Output directory")
    parser.add_argument(
        "-l",
        "--list",
        dest="list",
        required=False,
        default=False,
        action="store_true",
        help="List footage that can be exported",
    )
    parser.add_argument(
        "-m",
        "--master-only",
        dest="master_only",
        required=False,
        default=False,
        action="store_true",
        help="Parse only the master block",
    )
    args = parser.parse_args()

    source = args.input
    dest_folder = args.output
    if not os.path.isfile(source):
        print(f"File not found: {source}", file=sys.stderr)
        exit(1)
    if args.master_only:
        export_all_videos(source, None, list_only=False, master_only=True)
    elif args.list:
        export_all_videos(source, None, list_only=True, master_only=False)
    else:

        if dest_folder is None:
            print("Destination folder not specified", file=sys.stderr)
            exit(1)
        elif not os.path.isdir(dest_folder):
            print(f"{dest_folder} is not a directory", file=sys.stderr)
            exit(1)
        else:
            export_all_videos(source, dest_folder)
