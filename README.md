# VRCScreenShare Web

A small self-hosted screen streamer based on the capture and RTSP/TCP relay flow in VRCUtil's `VRCScreenShare` module. The UI is built with Svelte; MediaMTX receives browser WebRTC, and FFmpeg relays the stream to an RTSP/TCP path with AAC audio for VRChat players.

## Run with Docker Compose

1. Copy `.env.example` to `.env`.
2. Set `WEBRTC_ADDITIONAL_HOSTS` to the hostname or IP address viewers can reach. For local-only use, keep `localhost`.
3. Build and start the containers:

   ```sh
   docker compose up --build -d
   ```

4. Read the two generated addresses:

   ```sh
   docker compose logs app
   ```

   Open the **Owner URL** on the PC that will share its screen. It contains a private owner key. The **Viewer URL** is the six-character public route; the page also shows a TCP-only `rtspt://` address for VRChat.

The room code and owner key are saved in the `screenshare-data` volume, so restarting Compose keeps the URLs. Remove that volume only when you want a new room and key.

## Network setup

- The web page is served on `WEB_PORT` (default `8080`) and `WEB_BIND_ADDRESS` (default `0.0.0.0`). Put it behind HTTPS before using screen capture from another host; browsers only allow `getDisplayMedia()` on secure contexts such as HTTPS or localhost. In Coolify, route the `proxy` service on port `80` to your HTTPS domain and set `WEB_BIND_ADDRESS=127.0.0.1` to avoid publishing the plain HTTP port.
- Forward UDP `WEBRTC_PORT` (default `8189`) to the server. Set `WEBRTC_BIND_ADDRESS` (default `0.0.0.0`) and `WEBRTC_ADDITIONAL_HOSTS` to the public IP or DNS name so WebRTC clients receive a reachable ICE candidate. TCP `8189` is also exposed as a fallback.
- Forward TCP `RTSP_PORT` (default `8554`) from `RTSP_BIND_ADDRESS` (default `0.0.0.0`) for VRChat/RTSP viewers. RTSP is configured for TCP transport only.
- If using a TLS reverse proxy, set `PUBLIC_BASE_URL` to the public HTTPS origin and `PUBLIC_RTSP_HOST` to the public RTSP hostname.

The six-character room code is a convenient unlisted URL, not a password. Anyone with the Viewer URL can watch the stream. Only the Owner URL can publish a screen source.

## Development

```sh
npm install
npm run dev
```

`npm run build` creates the static Svelte frontend used by the Docker image. The Python server and MediaMTX are started through Docker Compose.

## License

This project is licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md) for runtime component notices.
