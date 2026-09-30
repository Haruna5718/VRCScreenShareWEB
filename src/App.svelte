<script>
	import { onMount } from "svelte";

	let video;
	let code = "";
	let live = false;
	let canPublish = false;
	let sharing = false;
	let busy = false;
	let error = "";
	let copied = "";
	let rtspAddress = "";
	let publisher = null;
	let captureStream = null;
	let publishAudioContext = null;
	let pollTimer;
	let copyTimer;

	const mediaUrl = (path, suffix) => `${location.origin}/media/${path}/${suffix}`;

	async function loadClass(url, name) {
		if (window[name]) return window[name];
		await new Promise((resolve, reject) => {
			const script = document.createElement("script");
			script.src = url;
			script.onload = resolve;
			script.onerror = () => reject(new Error(`Could not load ${name}.`));
			document.head.appendChild(script);
		});
		if (!window[name]) throw new Error(`${name} is unavailable.`);
		return window[name];
	}

	async function createOutgoingStream(sourceStream) {
		publishAudioContext = new AudioContext({ sampleRate: 48000 });
		const destination = publishAudioContext.createMediaStreamDestination();
		const silence = publishAudioContext.createGain();
		silence.gain.value = 0;
		const oscillator = publishAudioContext.createOscillator();
		oscillator.connect(silence);
		silence.connect(destination);
		oscillator.start();
		const capturedAudio = sourceStream.getAudioTracks()[0];
		if (capturedAudio) publishAudioContext.createMediaStreamSource(new MediaStream([capturedAudio])).connect(destination);
		try { await publishAudioContext.resume(); } catch {}
		return new MediaStream([...sourceStream.getVideoTracks(), ...destination.stream.getAudioTracks()]);
	}

	async function closePublishAudio() {
		if (!publishAudioContext) return;
		try { await publishAudioContext.close(); } catch {}
		publishAudioContext = null;
	}

	async function refreshSession() {
		try {
			const query = code ? "?code=" + encodeURIComponent(code) : "";
			const response = await fetch("/api/session" + query, { cache: "no-store" });
			if (!response.ok) throw new Error("The share session is unavailable.");
			const session = await response.json();
			if (!code) code = session.code;
			live = session.ready;
			canPublish = session.canPublish;
			rtspAddress = session.rtspUrl;
			error = "";
		} catch (cause) {
			error = cause instanceof Error ? cause.message : String(cause);
		}
	}

	async function stopShare() {
		const activePublisher = publisher;
		publisher = null;
		activePublisher?.close();
		await closePublishAudio();
		const previousStream = captureStream;
		captureStream = null;
		sharing = false;
		previousStream?.getTracks().forEach((track) => track.stop());
		if (video && video.srcObject === previousStream) video.srcObject = null;
		await refreshSession();
	}

	async function chooseSource() {
		if (!canPublish || busy) return;
		busy = true;
		error = "";
		let nextStream;
		let replaced = false;
		try {
			nextStream = await navigator.mediaDevices.getDisplayMedia({
				video: { frameRate: { ideal: 30, max: 30 }, width: { ideal: 1920 }, height: { ideal: 1080 } },
				audio: true,
			});
			publisher?.close();
			publisher = null;
			await closePublishAudio();
			captureStream?.getTracks().forEach((track) => track.stop());
			captureStream = nextStream;
			sharing = false;
			replaced = true;
			video.srcObject = nextStream;

			const Publisher = await loadClass(mediaUrl(`${code}_ingest`, "publisher.js"), "MediaMTXWebRTCPublisher");
			const outgoingStream = await createOutgoingStream(nextStream);
			await new Promise((resolve, reject) => {
				let settled = false;
				const timeout = setTimeout(() => finish(reject, new Error("The stream server did not respond.")), 20000);
				const finish = (callback, value) => {
					if (settled) return;
					settled = true;
					clearTimeout(timeout);
					callback(value);
				};
				publisher = new Publisher({
					url: mediaUrl(`${code}_ingest`, "whip"),
					stream: outgoingStream,
					videoCodec: "h264/90000",
					videoBitrate: 5000,
					audioCodec: "opus/48000",
					audioBitrate: 128,
					audioVoice: false,
					onConnected: () => finish(resolve),
					onError: (message) => finish(reject, new Error(String(message))),
				});
			});
			sharing = true;
			nextStream.getVideoTracks()[0]?.addEventListener("ended", stopShare, { once: true });
		} catch (cause) {
			if (replaced) {
				nextStream?.getTracks().forEach((track) => track.stop());
				if (captureStream === nextStream) captureStream = null;
				publisher?.close();
				publisher = null;
				await closePublishAudio();
				sharing = false;
				if (video && video.srcObject === nextStream) video.srcObject = null;
			}
			error = cause instanceof Error ? cause.message : String(cause);
			await refreshSession();
		} finally {
			busy = false;
		}
	}

	async function copy(value, label) {
		try {
			await navigator.clipboard.writeText(value);
			copied = label;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = ""), 1400);
		} catch {
			error = "Clipboard access was blocked by the browser.";
		}
	}

	onMount(() => {
		code = location.pathname.split("/").filter(Boolean)[0] || "";
		if (location.search) {
			history.replaceState(null, "", location.pathname);
		}
		sessionStorage.removeItem("vrc-screenshare-owner");
		refreshSession();
		pollTimer = setInterval(refreshSession, 1200);
		return () => {
			clearInterval(pollTimer);
			clearTimeout(copyTimer);
			publisher?.close();
			captureStream?.getTracks().forEach((track) => track.stop());
			closePublishAudio();
		};
	});
</script>

<svelte:head>
	<title>VRCScreenShare</title>
	<meta name="description" content="Send a live screen stream to VRChat over RTSP/TCP." />
</svelte:head>

<main>
	<section class="share-card" aria-label="Screen share">
		<div class="preview" class:has-stream={live || sharing}>
			<video bind:this={video} autoplay muted playsinline></video>
			{#if canPublish}
				<button class="source-overlay" onclick={chooseSource} disabled={busy} aria-label="Select or change screen source">
					<span>{busy ? "Connecting…" : sharing ? "Click to Change source" : "Click to Select source"}</span>
				</button>
			{:else if !live}
				<div class="offline-overlay"><span>Waiting for source</span></div>
			{/if}
		</div>

		<div class="links-panel">
			<div class="link-row">
				<div class="link-pill">
					<span class="status-dot" class:online={live} title={live ? "Streaming" : "Stopped"} aria-label={live ? "Streaming" : "Stopped"}></span>
					<span class="link-value">{rtspAddress || "Connecting…"}</span>
				</div>
				<button class="copy-button" onclick={() => copy(rtspAddress, "rtsp")} disabled={!rtspAddress} aria-label="Copy RTSP/TCP URL" title="Copy RTSP/TCP URL">
					{copied === "rtsp" ? "" : ""}
				</button>
			</div>
			{#if error}
				<p class="error-message" role="status">{error}</p>
			{/if}
		</div>
	</section>
</main>
