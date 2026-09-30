<script>
	import { onMount } from "svelte";

	let video;
	let code = "";
	let live = false;
	let sharing = false;
	let busy = false;
	let notices = [];
	let nextNoticeId = 0;
	let copied = "";
	let rtspAddress = "";
	let publisher = null;
	let captureStream = null;
	let publishAudioContext = null;
	let pollTimer;
	let copyTimer;
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

	function showNotice(level, title, message) {
		if (notices.some((notice) => notice.title === title && notice.message === message)) return;
		notices = [...notices, { id: nextNoticeId++, level, title, message }];
	}

	function dismissNotice(id) {
		notices = notices.filter((notice) => notice.id !== id);
	}

	function noticeIcon(level) {
		return ["", "", "", ""][level] || "";
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
			showNotice(3, "Connection failed", cause instanceof Error ? cause.message : String(cause));
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
			const cancelled = cause instanceof DOMException && cause.name === "NotAllowedError";
			showNotice(cancelled ? 2 : 3, cancelled ? "Source not selected" : "Screen sharing failed", cause instanceof Error ? cause.message : String(cause));
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
			showNotice(1, "Copied", "The Streaming address is on your clipboard.");
		} catch {
			showNotice(3, "Copy failed", "Clipboard access was blocked by the browser.");
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

{#if notices.length}
	<div class="notice-stack" aria-live="polite">
		{#each notices as item (item.id)}
			<aside class="notice" class:l0={item.level === 0} class:l1={item.level === 1} class:l2={item.level === 2} class:l3={item.level === 3} role={item.level === 3 ? "alert" : "status"}>
				<span class="notice-icon" aria-hidden="true">{noticeIcon(item.level)}</span>
				<span class="notice-content">
					<span class="notice-title">{item.title}</span>
					<span class="notice-description">{item.message}</span>
				</span>
				<button class="notice-close" onclick={() => dismissNotice(item.id)} aria-label="Dismiss"></button>
			</aside>
		{/each}
	</div>
{/if}
