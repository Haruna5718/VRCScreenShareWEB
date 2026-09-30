# VRCScreenShare Web

A small public screen streamer based on the capture and RTSP/TCP relay flow in VRCUtil's `VRCScreenShare` module. The main Svelte page is the shared room: anyone can open it, select a screen, and publish. MediaMTX receives the browser stream, and FFmpeg relays it to an RTSP/TCP path with AAC audio for VRChat players.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `WEBRTC_ADDITIONAL_HOSTS` to the hostname or IP address viewers can reach. For local-only use, keep `localhost`.
3. Build and start the containers:

   ```sh
   docker compose -f compose.yaml -f compose.standalone.yaml up --build -d
   ```

4. Open the main `PUBLIC_BASE_URL` to select a screen. The app logs the RTSP/TCP address:

   ```sh
   docker compose logs app
   ```

   The page shows the same TCP-only `rtspt://` address that its copy button copies.

The six-character room code and internal relay credentials are saved in the `screenshare-data` volume, so restarting Compose keeps the addresses. Remove that volume only when you want a new room.

## Network setup

- The web page listens on port `8000` in the container. Coolify routes the app service address to that port; the main Compose file does not bind an extra host port. For standalone use, `compose.standalone.yaml` publishes it on `WEB_PORT` (default `8080`) and `WEB_BIND_ADDRESS` (default `0.0.0.0`). Put it behind HTTPS before using screen capture from another host; browsers only allow `getDisplayMedia()` on secure contexts such as HTTPS or localhost.
- Forward UDP `WEBRTC_PORT` (default `8189`) to the server. Set `WEBRTC_BIND_ADDRESS` (default `0.0.0.0`) and `WEBRTC_ADDITIONAL_HOSTS` to the public IP or DNS name so WebRTC clients receive a reachable ICE candidate. TCP `8189` is also exposed as a fallback.
- Forward TCP `RTSP_PORT` (default `8554`) from `RTSP_BIND_ADDRESS` (default `0.0.0.0`) for VRChat/RTSP viewers. RTSP is configured for TCP transport only.
- Set `PUBLIC_BASE_URL` to the public HTTPS origin and `PUBLIC_RTSP_HOST` to the public RTSP host. If the web hostname is behind an HTTP-only CDN proxy, use the server's public IP for `PUBLIC_RTSP_HOST` and `WEBRTC_ADDITIONAL_HOSTS` so RTSP and WebRTC media bypass that proxy.

The six-character room path is unlisted, not a password. Anyone who can reach the main page can publish to the shared room or watch its RTSP stream. MediaMTX accepts one active publisher per room.

## Development

```sh
npm install
npm run dev
```

`npm run build` creates the static Svelte frontend used by the Docker image. The Python server and MediaMTX are started through Docker Compose.

## License

This project is licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md) for runtime component notices.
