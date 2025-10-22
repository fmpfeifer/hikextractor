<img width="1002" height="732" alt="{C24665C8-202E-4A47-A3FF-ABE22AA1BF85}" src="https://github.com/user-attachments/assets/07d83f44-78d1-4110-84ed-1f4e250d7833" />

# hikextractor-gui

Script to parse HIKVISION DVR hard drives and export the footage

This script was written as an effort to extract footage from a specific DVR (JFL TradeMark, model DHD-2104N). It's HD contained strings that identify it as **HIKVISION, version HIK.2011.03.08**.
This script was written based on the following paper: [Paper](https://eudl.eu/pdf/10.1007/978-3-319-25512-5_13).

The format found in the HD was not the same as the described in the paper, but the overall structure was the same (maybe a different version).

It was tested only in **windows**, using a **DD image** of the HD as input.
It uses **FFmpeg** to mux the video into MP4 files, so FFmpeg should be in the OS search path (or `ffmpeg.exe` in the same folder as the script is enough). You can get a copy of FFmpeg here: [FFmpeg](https://ffmpeg.org/download.html).

The script was tested using **python 3.9**, but any version from 3.7 on should work.

The HD Image can be created using `dd` (from linux), any forensic imager (FTK Imager, for instance). You can also use the HDD Raw Copy Tool from HDDGURU: [HDD Raw Copy Tool](https://hddguru.com/software/HDD-Raw-Copy-Tool/).

---

## Usage

### Decode master block (CLI)

In the folder containing the file **`hikextractor.py`**:

```sh
python hikextractor.py -i <INPUT_IMAGE.DD> -m
````

### List available footage (CLI)

In the folder containing the file **`hikextractor.py`**:

```sh
python hikextractor.py -i <INPUT_IMAGE.DD> -l
```

### Run Graphical User Interface (GUI) 🖥️

For a user-friendly, visual experience, run the dedicated GUI script. This simplifies selecting the input image and output directory.

In the folder containing the file **`hikextractor-gui.py`**:

```sh
python hikextractor-gui.py
```

  - A window will open, allowing you to select the **HD raw image** and the **output folder**.
  - **FFmpeg** must still be accessible for the extraction process to work.

### Extract all footage (CLI)

In the folder containing the file **`hikextractor.py`** and **`ffmpeg.exe`**:

```sh
python hikextractor.py -i <INPUT_IMAGE.DD> -o <OUTPUT_DIR>
```

  - **`INPUT_IMAGE.DD`**: HD raw image file.
  - **`OUTPUT_DIR`**: Output folder where the MP4 videos will be saved.

### Extract all footage to raw h264 Stream (CLI)

If FFmpeg cannot mux the H264 stream to an MP4 file, you can export the raw H264 stream instead.
It can be played using the VLC Media Player.

```winget install --id=Gyan.FFmpeg  -e```

In the folder containing the file **`hikextractor.py`** and **`ffmpeg.exe`**:

```sh
python hikextractor.py -i <INPUT_IMAGE.DD> -o <OUTPUT_DIR> -r
```

  - **`INPUT_IMAGE.DD`**: HD raw image file.
  - **`OUTPUT_DIR`**: Output folder where the raw H264 video files will be saved.

<!-- end list -->
