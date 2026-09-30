# VRCScreenShare Web

A small self-hosted screen streamer based on the capture and RTSP/TCP relay flow in VRCUtil's `VRCScreenShare` module. The Svelte page captures a screen and shows a local preview; MediaMTX receives the browser stream, and FFmpeg relays it to an RTSP/TCP path with AAC audio for VRChat players. Viewers connect through RTSP/TCP; the web page is not a viewer.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `WEBRTC_ADDITIONAL_HOSTS` to the hostname or IP address viewers can reach. For local-only use, keep `localhost`.
3. Build and start the containers:

   ```sh
   docker compose up --build -d
   ```

4. Read the generated addresses:

   ```sh
   docker compose logs app
   ```

   Open the **Owner URL** on the PC that will share its screen. It contains a private owner key. The page shows a TCP-only `rtspt://` address for VRChat viewers.

The room code and owner key are saved in the `screenshare-data` volume, so restarting Compose keeps the URLs. Remove that volume only when you want a new room and key.

## Network setup

- The web page listens on port `8000` in the container; standalone Compose publishes it on `WEB_PORT` (default `8080`) and `WEB_BIND_ADDRESS` (default `0.0.0.0`). Put it behind HTTPS before using screen capture from another host; browsers only allow `getDisplayMedia()` on secure contexts such as HTTPS or localhost. In Coolify, route the `app` service on port `8000` to your HTTPS domain and set `WEB_BIND_ADDRESS=127.0.0.1` to avoid publishing the plain HTTP port.
- Forward UDP `WEBRTC_PORT` (default `8189`) to the server. Set `WEBRTC_BIND_ADDRESS` (default `0.0.0.0`) and `WEBRTC_ADDITIONAL_HOSTS` to the public IP or DNS name so WebRTC clients receive a reachable ICE candidate. TCP `8189` is also exposed as a fallback.
- Forward TCP `RTSP_PORT` (default `8554`) from `RTSP_BIND_ADDRESS` (default `0.0.0.0`) for VRChat/RTSP viewers. RTSP is configured for TCP transport only.
- Set `PUBLIC_BASE_URL` to the public HTTPS origin and `PUBLIC_RTSP_HOST` to the public RTSP host. If the web hostname is behind an HTTP-only CDN proxy, use the server's public IP for `PUBLIC_RTSP_HOST` and `WEBRTC_ADDITIONAL_HOSTS` so RTSP and WebRTC media bypass that proxy.

The six-character RTSP path is unlisted, not a password; anyone who knows the RTSP URL can watch the stream. Only the Owner URL can publish a screen source.

## Development

```sh
npm install
npm run dev
```

`npm run build` creates the static Svelte frontend used by the Docker image. The Python server and MediaMTX are started through Docker Compose.

## License

This project is licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md) for runtime component notices.
