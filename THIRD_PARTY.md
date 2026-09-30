# Third-party components

- [MediaMTX](https://github.com/bluenviron/mediamtx) is used as the WebRTC, SRT, and RTSP media server. Its container image is MIT-licensed.
- FFmpeg is installed from the Debian Bookworm package repository in the application image. FFmpeg and its enabled codecs retain their own licenses; check `ffmpeg -L` and Debian package metadata when redistributing built images.
