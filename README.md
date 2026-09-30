# VRCScreenShare Web

A small public screen streamer based on the capture and RTSP/TCP relay flow in VRCUtil's `VRCScreenShare` module. Each browser tab gets its own six-character room ID, can select a screen, and publish. MediaMTX receives the browser stream, and FFmpeg relays it to an RTSP/TCP path with AAC audio for VRChat players.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `WEBRTC_ADDITIONAL_HOSTS` to the hostname or IP address viewers can reach. For local-only use, keep `localhost`.
3. Build and start the containers:

   ```sh
   docker compose -f compose.yaml -f compose.standalone.yaml up --build -d
   ```

4. Open the main `PUBLIC_BASE_URL` to select a screen. The page shows the session's TCP-only `rtspt://` address and its copy button copies that same address.

The room ID is scoped to a browser tab and survives reloads in that tab. Opening the main page in another tab or browser creates a different ID. Inactive sessions expire after six hours; the internal relay credential is saved in the `screenshare-data` volume.

## Network setup

- The web page listens on port `8000` in the container. Coolify routes the app service address to that port; the main Compose file does not bind an extra host port. For standalone use, `compose.standalone.yaml` publishes it on `WEB_PORT` (default `8080`) and `WEB_BIND_ADDRESS` (default `0.0.0.0`). Put it behind HTTPS before using screen capture from another host; browsers only allow `getDisplayMedia()` on secure contexts such as HTTPS or localhost.
- Forward UDP `WEBRTC_PORT` (default `8189`) to the server. Set `WEBRTC_BIND_ADDRESS` (default `0.0.0.0`) and `WEBRTC_ADDITIONAL_HOSTS` to the public IP or DNS name so WebRTC clients receive a reachable ICE candidate. TCP `8189` is also exposed as a fallback.
- Forward TCP `RTSP_PORT` (default `554`) from `RTSP_BIND_ADDRESS` (default `0.0.0.0`) for VRChat/RTSP viewers. The copied `rtspt://` URL omits the port and uses TCP 554; the media server listens on container port `8554`.
- Set `PUBLIC_BASE_URL` to the public HTTPS origin and `PUBLIC_RTSP_HOST` to the public RTSP host. The RTSP hostname must resolve directly to the server or use Cloudflare Spectrum; Cloudflare's standard HTTP proxy does not proxy RTSP/TCP on port `554`. Set `WEBRTC_ADDITIONAL_HOSTS` to a public IP or hostname reachable by WebRTC clients.

The six-character room path is unlisted, not a password. Anyone who has a session's RTSP URL can watch that stream. Each session accepts one active publisher.

## Development

```sh
npm install
npm run dev
```

`npm run build` creates the static Svelte frontend used by the Docker image. The Python server and MediaMTX are started through Docker Compose.

## License

This project is licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md) for runtime component notices.
