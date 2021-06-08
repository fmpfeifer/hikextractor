# hikextractor

Script to parse HIKVISION DVR hard drives and export the footage

This script was written as an effort to extract footage from a specific DVR (JFL TradeMark, modelo DHD-2104N). It's HD contained strings that identify it as HIKVISION, version HIK.2011.03.08.
This script was written based on the following paper: [Paper](https://eudl.eu/pdf/10.1007/978-3-319-25512-5_13).
The format found in the HD was not the same as the described in the paper, but the overall structure was the same (maybe a different version).

It was tested only in windows, using and DD image of the HD as input.
It uses ffmpeg to mux the video into MP4 files, so ffmpeg should be in the os search path (ffmpeg.exe in the same folder as the script is enough).
