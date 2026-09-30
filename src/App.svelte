<script>
	import { onMount } from "svelte";

	let video;
	let code = "";
	let live = false;
	let sharing = false;
	let busy = false;
	let notice = null;
	let copied = "";
	let rtspAddress = "";
	let publisher = null;
	let captureStream = null;
	let publishAudioContext = null;
	let pollTimer;
	let copyTimer;
	let noticeTimer;
	let createSessionRequest = null;
	const sessionKey = "vrc-screenshare-session";

	const mediaUrl = (path, suffix) => `${location.origin}/media/${path}/${suffix}`;
	const saveSessionCode = (value) => { try { sessionStorage.setItem(sessionKey, value); } catch {} };
	const clearSessionCode = () => { try { sessionStorage.removeItem(sessionKey); } catch {} };

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

	function showNotice(severity, title, message) {
		if (notice?.title === title && notice?.message === message) return;
		notice = { severity, title, message };
		clearTimeout(noticeTimer);
		noticeTimer = setTimeout(() => (notice = null), 6000);
	}

	function acceptSession(session) {
		code = session.code;
		saveSessionCode(code);
		live = session.ready;
		rtspAddress = session.rtspUrl;
	}

	async function createSession() {
		if (createSessionRequest) return createSessionRequest;
		createSessionRequest = (async () => {
			const response = await fetch("/api/session", { method: "POST", cache: "no-store" });
			if (!response.ok) throw new Error("Could not create a share session.");
			acceptSession(await response.json());
		})();
		try { await createSessionRequest; } finally { createSessionRequest = null; }
	}

	async function refreshSession() {
		try {
			if (!code) return await createSession();
			const response = await fetch("/api/session?code=" + encodeURIComponent(code), { cache: "no-store" });
			if (response.status === 404) {
				code = "";
				clearSessionCode();
				return await createSession();
			}
			if (!response.ok) throw new Error("The share session is unavailable.");
			const session = await response.json();
			acceptSession(session);
		} catch (cause) {
			showNotice("critical", "Connection failed", cause instanceof Error ? cause.message : String(cause));
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
		if (busy) return;
		busy = true;
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
			showNotice("critical", "Screen sharing failed", cause instanceof Error ? cause.message : String(cause));
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
			showNotice("success", "Copied", "The RTSP/TCP address is on your clipboard.");
		} catch {
			showNotice("critical", "Copy failed", "Clipboard access was blocked by the browser.");
		}
	}

	onMount(() => {
		const navigationType = performance.getEntriesByType("navigation")[0]?.type;
		if (navigationType === "reload" || navigationType === "back_forward") {
			try { code = sessionStorage.getItem(sessionKey) || ""; } catch { code = ""; }
		}
		refreshSession();
		pollTimer = setInterval(refreshSession, 1200);
		return () => {
			clearInterval(pollTimer);
			clearTimeout(copyTimer);
			clearTimeout(noticeTimer);
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
			<button class="source-overlay" onclick={chooseSource} disabled={busy} aria-label="Select or change screen source">
				<span>{busy ? "Connecting…" : sharing ? "Click to Change source" : "Click to Select source"}</span>
			</button>
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
		</div>
	</section>
</main>

{#if notice}
	<aside class="notice" class:success={notice.severity === "success"} class:caution={notice.severity === "caution"} class:critical={notice.severity === "critical"} role={notice.severity === "critical" ? "alert" : "status"} aria-live={notice.severity === "critical" ? "assertive" : "polite"}>
		<span class="notice-icon" aria-hidden="true">{notice.severity === "success" ? "" : notice.severity === "caution" ? "" : ""}</span>
		<div class="notice-copy">
			<strong>{notice.title}</strong>
			<span>{notice.message}</span>
		</div>
		<button class="notice-close" onclick={() => (notice = null)} aria-label="Dismiss"></button>
	</aside>
{/if}
