<script>
	import { onMount } from 'svelte';
	import { buildAudioGroups } from '$lib/audio-catalog.js';
	import { CHARGE_SOUND_DURATION_SECONDS, POSITIONS, STATES, STANDS, UrnEngine } from '$lib/urn-engine.js';

	const pixelIndices = Array.from({ length: 16 }, (_, index) => index);

	const engine = new UrnEngine({ fillTime: CHARGE_SOUND_DURATION_SECONDS, setDownDelay: 2.5 });
	let revision = $state(0);
	let logs = $state([]);
	let audioGroups = $state({});
	let audioAssetCount = $state(0);
	let audioLoaded = $state(false);
	let audioLoadError = $state('');
	let audioQueue = $state([]);
	let pendingAudioRequests = [];
	let nowPlaying = $state(null);
	let lastPlayed = $state(null);
	let audioElement = $state(null);
	let ambientElement = $state(null);
	let jingleElementA = $state(null);
	let jingleElementB = $state(null);
	let ambientStage = $state('');
	let audioProgress = $state(0);
	let soundEnabled = $state(true);
	let volume = $state(0.8);
	let simSpeed = $state(1);
	let visualPulse = $state('');
	let visualPulseTimer;
	let lastTrackByGroup = {};
	let nextJingleAtMs = Infinity;
	let jingleVoiceIndex = 0;
	const fillingStartPath = '/audio/soul_urn_pickup/soul_urn_cashin_timer_01.mp3';
	const fillingLoopPath = '/audio/soul_urn_pickup/soul_urn_cashin_timer_lp_01.mp3';
	const ambientVolume = 0.34;
	const carryingJingleVolume = 0.72;
	const carryingJingleDuckedVolume = 0.24;

	let view = $derived.by(() => {
		revision;
		return {
			powered: engine.powered,
			state: engine.state,
			stateLabel: engine.stateLabel,
			position: engine.position,
			source: engine.source,
			destination: engine.destination,
			phase: engine.phase,
			fillFraction: engine.fillFraction,
			fillPercent: Math.round(engine.fillFraction * 100),
			chargingActive: engine.chargingActive,
			simTime: engine.simTime,
			pendingSetDown: engine.pendingSetDownAt !== null
		};
	});

	function formatTime(seconds) {
		const minutes = Math.floor(seconds / 60);
		const remainder = Math.floor(seconds % 60);
		return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
	}

	function titleCase(value) {
		if (!value) return '—';
		return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
	}

	function trackTitle(path) {
		const filename = path.split('/').at(-1)?.replace('.mp3', '') ?? path;
		return filename
			.replace(/^\d+_/, '')
			.replaceAll('_', ' ')
			.replace(/\b\w/g, (letter) => letter.toUpperCase());
	}

	function pushLog(event) {
		logs = [event, ...logs].slice(0, 80);
	}

	function triggerPulse(name) {
		visualPulse = name;
		clearTimeout(visualPulseTimer);
		visualPulseTimer = setTimeout(() => {
			visualPulse = '';
		}, name === 'delivery' ? 3200 : 1300);
	}

	function consumeEvents() {
		for (const event of engine.drainEvents()) {
			pushLog(event);
			if (['unhomed', 'fill_start', 'fill_resume', 'fill_paused', 'wrong_pad', 'pickup', 'delivery', 'wrong_source', 'gentle_setdown', 'hard_drop', 'urn_expired'].includes(event.type)) {
				interruptVoicePlayback();
			}
			if (event.type === 'pickup' && event.audio.includes('pickup_effect')) triggerPulse('pickup');
			if (event.type === 'delivery') triggerPulse('delivery');
			if (event.type === 'hard_drop' || event.type === 'unhomed' || event.type === 'wrong_pad') triggerPulse('impact');
			if (event.audio.length) enqueueAudio(event.audio, event);
		}
		reconcileAmbience();
		reconcileCarryingJingles();
		revision += 1;
	}

	function run(action) {
		action();
		consumeEvents();
	}

	function chooseTrack(groupName) {
		const tracks = audioGroups[groupName] ?? [];
		if (!tracks.length) return null;
		let track = tracks[Math.floor(Math.random() * tracks.length)];
		if (tracks.length > 1 && track === lastTrackByGroup[groupName]) {
			track = tracks[(tracks.indexOf(track) + 1) % tracks.length];
		}
		lastTrackByGroup[groupName] = track;
		return { path: track, group: groupName, title: trackTitle(track) };
	}

	function enqueueAudio(groupNames, event) {
		if (!soundEnabled) return;
		if (event.type === 'delivery') engine.suspendRefillUntilDeliveryAudioEnds();
		if (groupNames.some((group) => group !== 'pickup_effect' && group !== 'dropoff_effect')) {
			engine.suspendChatter();
		}
		if (!audioLoaded) {
			pendingAudioRequests.push({ groupNames, event });
			return;
		}
		const additions = groupNames
			.map((groupName) => chooseTrack(groupName))
			.filter(Boolean)
			.map((track) => ({ ...track, eventType: event.type }));
		audioQueue = [...audioQueue, ...additions];
		setTimeout(startNextAudio, 0);
	}

	async function startNextAudio() {
		if (!audioElement || nowPlaying || !audioQueue.length || !soundEnabled || !engine.powered) return;
		const [next, ...rest] = audioQueue;
		audioQueue = rest;
		nowPlaying = next;
		lastPlayed = next;
		if (isSpokenTrack(next)) engine.suspendChatter();
		audioProgress = 0;
		audioElement.src = next.path;
		audioElement.volume = volume;
		updateJingleDucking(true);
		try {
			await audioElement.play();
		} catch (error) {
			pushLog({ id: `audio-${Date.now()}`, time: engine.simTime, type: 'audio_error', message: `Browser blocked audio: ${error.message}`, tone: 'danger' });
			const failed = nowPlaying;
			nowPlaying = null;
			audioProgress = 0;
			updateJingleDucking(false);
			finishPlaybackTiming(failed);
			setTimeout(startNextAudio, 0);
		}
	}

	function isSpokenTrack(track) {
		return Boolean(track) && track.group !== 'pickup_effect' && track.group !== 'dropoff_effect';
	}

	function shouldPlayFillingAmbience() {
		return soundEnabled && engine.chargingActive;
	}

	function reconcileAmbience() {
		if (shouldPlayFillingAmbience()) {
			if (!['loading', 'start', 'loop'].includes(ambientStage)) startFillingAmbience();
			return;
		}
		stopAmbience();
	}

	async function startFillingAmbience() {
		if (!ambientElement || !shouldPlayFillingAmbience()) return;
		ambientElement.pause();
		ambientElement.loop = false;
		ambientElement.src = fillingStartPath;
		ambientElement.load();
		ambientElement.volume = ambientVolume;
		ambientStage = 'loading';
		try {
			if (ambientElement.readyState < 1) {
				await new Promise((resolve, reject) => {
					ambientElement.addEventListener('loadedmetadata', resolve, { once: true });
					ambientElement.addEventListener('error', () => reject(new Error('Could not load charging sound metadata.')), { once: true });
				});
			}
			if (ambientStage !== 'loading' || !shouldPlayFillingAmbience()) return;
			ambientElement.currentTime = Math.min(engine.fillElapsed, CHARGE_SOUND_DURATION_SECONDS);
			ambientStage = 'start';
			await ambientElement.play();
		} catch (error) {
			if (!ambientStage) return;
			pushLog({ id: `ambient-${Date.now()}`, time: engine.simTime, type: 'audio_error', message: `Browser blocked filling ambience: ${error.message}`, tone: 'danger' });
			ambientStage = '';
		}
	}

	async function handleAmbientEnded() {
		if (ambientStage !== 'start') return;
		if (!shouldPlayFillingAmbience()) {
			clearAmbience();
			return;
		}
		ambientStage = 'loop';
		ambientElement.src = fillingLoopPath;
		ambientElement.loop = true;
		ambientElement.volume = ambientVolume;
		try {
			await ambientElement.play();
		} catch (error) {
			pushLog({ id: `ambient-loop-${Date.now()}`, time: engine.simTime, type: 'audio_error', message: `Could not start filling loop: ${error.message}`, tone: 'danger' });
			ambientStage = '';
		}
	}

	function clearAmbience() {
		if (!ambientElement) return;
		ambientElement.pause();
		ambientElement.loop = false;
		ambientElement.removeAttribute('src');
		ambientElement.load();
		ambientElement.volume = ambientVolume;
		ambientStage = '';
	}

	function stopAmbience() {
		if (!ambientElement || !ambientStage) return;
		clearAmbience();
	}

	function shouldPlayCarryingJingles() {
		return soundEnabled
			&& audioLoaded
			&& engine.powered
			&& engine.state === STATES.CARRIED
			&& engine.position === POSITIONS.CARRIED;
	}

	function jingleElements() {
		return [jingleElementA, jingleElementB].filter(Boolean);
	}

	function reconcileCarryingJingles() {
		if (shouldPlayCarryingJingles()) {
			if (nextJingleAtMs === Infinity) nextJingleAtMs = performance.now() + 180;
			return;
		}
		stopCarryingJingles();
	}

	function updateCarryingJingles(timestamp) {
		if (!shouldPlayCarryingJingles()) return;
		if (timestamp < nextJingleAtMs) return;
		playCarryingJingle();
		// These are individual footstep sweeteners, so vary the running cadence.
		nextJingleAtMs = timestamp + 380 + Math.random() * 240;
	}

	async function playCarryingJingle() {
		const track = chooseTrack('carrying_jingle');
		const elements = jingleElements();
		if (!track || !elements.length) return;
		const element = elements[jingleVoiceIndex % elements.length];
		jingleVoiceIndex += 1;
		element.pause();
		element.src = track.path;
		element.volume = nowPlaying ? carryingJingleDuckedVolume : carryingJingleVolume;
		try {
			await element.play();
		} catch {
			// A later user interaction will release browser autoplay blocking.
		}
	}

	function updateJingleDucking(voiceActive) {
		const level = voiceActive ? carryingJingleDuckedVolume : carryingJingleVolume;
		for (const element of jingleElements()) element.volume = level;
	}

	function stopCarryingJingles() {
		if (nextJingleAtMs === Infinity) return;
		nextJingleAtMs = Infinity;
		for (const element of jingleElements()) {
			element.pause();
			element.removeAttribute('src');
			element.load();
		}
	}

	function handleAudioEnded() {
		const finished = nowPlaying;
		nowPlaying = null;
		audioProgress = 0;
		updateJingleDucking(false);
		finishPlaybackTiming(finished);
		setTimeout(startNextAudio, 0);
	}

	function finishPlaybackTiming(track) {
		if (track?.group === 'delivered') engine.scheduleRefillAfterDeliveryAudio();
		if (isSpokenTrack(track)) engine.resumeChatterAfterDialogue();
	}

	function handleAudioError() {
		if (nowPlaying) {
			pushLog({ id: `audio-file-${Date.now()}`, time: engine.simTime, type: 'audio_error', message: `Could not play ${nowPlaying.path}`, tone: 'danger' });
		}
		handleAudioEnded();
	}

	function interruptVoicePlayback() {
		if (audioElement) {
			audioElement.pause();
			audioElement.removeAttribute('src');
			audioElement.load();
		}
		audioQueue = [];
		pendingAudioRequests = [];
		nowPlaying = null;
		audioProgress = 0;
		updateJingleDucking(false);
	}

	function stopAudio() {
		interruptVoicePlayback();
		engine.scheduleRefillAfterDeliveryAudio();
		lastPlayed = null;
		lastTrackByGroup = {};
		stopAmbience();
		stopCarryingJingles();
	}

	function powerOff() {
		stopAudio();
		run(() => engine.powerOff());
	}

	function resetJourney() {
		stopAudio();
		run(() => engine.reset());
	}

	function toggleSound() {
		soundEnabled = !soundEnabled;
		if (!soundEnabled) {
			stopAudio();
			engine.resumeChatterAfterDialogue();
		}
		else {
			reconcileAmbience();
			reconcileCarryingJingles();
		}
	}

	function replayLast() {
		if (!canReplayLast()) return;
		audioQueue = [...audioQueue, { ...lastPlayed }];
		startNextAudio();
	}

	function canReplayLast() {
		if (!lastPlayed || !soundEnabled || !engine.powered || !engine.isFull) return false;
		const onPad = engine.position === STANDS.NORTH || engine.position === STANDS.SOUTH;
		if (lastPlayed.group === 'wait_on_pad') return onPad && (engine.state === STATES.READY || engine.state === STATES.WRONG_SOURCE);
		if (lastPlayed.group === 'wait_off_pad' || lastPlayed.group === 'hard_drop') return engine.phase === 'transit' && engine.position === POSITIONS.GROUND;
		if (['pickup_effect', 'pickup_from_pad', 'pickup_from_ground', 'carrying'].includes(lastPlayed.group)) return engine.phase === 'transit' && engine.position === POSITIONS.CARRIED;
		if (lastPlayed.group === 'urn_expires') return engine.phase === 'transit';
		return false;
	}

	function skipAudio() {
		const skipped = nowPlaying;
		if (audioElement) audioElement.pause();
		nowPlaying = null;
		audioProgress = 0;
		updateJingleDucking(false);
		finishPlaybackTiming(skipped);
		startNextAudio();
	}

	function fillPixelActive(ring, index) {
		if (!view.powered) return false;
		if (visualPulse) return true;
		if (view.state === STATES.UNHOMED) return true;
		if (view.state === STATES.FILLING || view.state === STATES.FILL_PAUSED) {
			const absoluteIndex = ring === 'bottom' ? index : index + 16;
			return absoluteIndex < Math.ceil(view.fillFraction * 32);
		}
		return view.phase === 'ready' || view.phase === 'transit';
	}

	function pixelColor(ring, index) {
		if (!view.powered) return '#111923';
		if (visualPulse === 'impact') return index % 2 ? '#ff3f2f' : '#ffb262';
		if (visualPulse === 'delivery') return index % 3 ? '#77ffd1' : '#fff7c2';
		if (visualPulse === 'pickup') return index % 4 ? '#3fffd1' : '#ecfff9';
		if (view.state === STATES.WRONG_SOURCE) return index % 4 < 2 ? '#ff5d31' : '#5f181b';
		if (view.state === STATES.FILL_PAUSED) return index % 2 ? '#ca62ff' : '#ffad4b';
		if (view.state === STATES.RESTING_WRONG) return index % 4 ? '#8e54c8' : '#ffad4b';
		if (view.state === STATES.UNHOMED) return ring === 'bottom' ? '#55a8ff' : '#ca62ff';
		const palette = ['#36ffd1', '#4ad5ff', '#9371ff', '#ddfff5'];
		return palette[(index + (ring === 'top' ? 1 : 0)) % palette.length];
	}

	onMount(() => {
		consumeEvents();
		fetch('/audio/manifest.json')
			.then((response) => {
				if (!response.ok) throw new Error(`HTTP ${response.status}`);
				return response.json();
			})
			.then(({ files }) => {
				audioGroups = buildAudioGroups(files);
				audioAssetCount = files.length;
				audioLoaded = true;
				reconcileCarryingJingles();
				const waiting = pendingAudioRequests;
				pendingAudioRequests = [];
				for (const request of waiting) enqueueAudio(request.groupNames, request.event);
			})
			.catch((error) => {
				audioLoadError = error.message;
				engine.scheduleRefillAfterDeliveryAudio();
			});

		let previous = performance.now();
		let accumulated = 0;
		let frame;
		const tick = (timestamp) => {
			const realDelta = Math.min(0.1, (timestamp - previous) / 1000);
			previous = timestamp;
			if (engine.powered) {
				accumulated += realDelta * simSpeed;
				if (accumulated >= 0.05) {
					engine.advance(accumulated);
					accumulated = 0;
					consumeEvents();
				}
			}
			updateCarryingJingles(timestamp);
			frame = requestAnimationFrame(tick);
		};
		frame = requestAnimationFrame(tick);
		return () => {
			cancelAnimationFrame(frame);
			clearTimeout(visualPulseTimer);
			stopCarryingJingles();
		};
	});
</script>

<svelte:head>
	<title>Soul Urn Field Simulator</title>
	<meta name="description" content="Interactive state, light, sensor, and audio simulator for the physical Soul Urn prop." />
</svelte:head>

<audio bind:this={audioElement} onended={handleAudioEnded} onerror={handleAudioError} ontimeupdate={() => { audioProgress = audioElement?.duration ? audioElement.currentTime / audioElement.duration : 0; }}></audio>
<audio bind:this={ambientElement} preload="auto" onended={handleAmbientEnded}></audio>
<audio bind:this={jingleElementA} preload="auto"></audio>
<audio bind:this={jingleElementB} preload="auto"></audio>

<main class="app-shell">
	<header class="topbar">
		<div class="brand-lockup">
			<div class="coven-mark" aria-hidden="true"><span></span><span></span><span></span></div>
			<div><p class="eyebrow">Municipal Coven · Artifact Lab 04</p><h1>Soul Urn <em>Field Simulator</em></h1></div>
		</div>
		<div class="header-status">
			<span class:online={view.powered} class="status-dot"></span>
			<div><strong>{view.powered ? 'SYSTEM LIVE' : 'SYSTEM SAFE'}</strong><small>SIM {formatTime(view.simTime)} · {simSpeed}×</small></div>
		</div>
	</header>

	<section class="workspace">
		<div class="simulator-column">
			<div class="section-heading">
				<div><p class="eyebrow">Live artifact model</p><h2>{view.stateLabel}</h2></div>
				<div class="phase-chip phase-{view.phase}">{titleCase(view.phase)}</div>
			</div>

			<div class="stage state-{view.state} pulse-{visualPulse}">
				<div class="stage-grid"></div>
				<div class="route-readout">
					<div class:active={view.source === STANDS.NORTH || view.source === STANDS.SOUTH}><span>Source</span><strong>{view.source ? titleCase(view.source) : 'Unassigned'}</strong></div>
					<div class="route-arrow"><span></span></div>
					<div class:active={Boolean(view.destination)}><span>Required destination</span><strong>{view.destination ? titleCase(view.destination) : '—'}</strong></div>
				</div>

				<div class="stand stand-north" class:occupied={view.position === STANDS.NORTH}>
					<div class="magnet-field north-field"></div><div class="pad-disc"><span>N</span></div><p>North-up pad</p><small>+Z field</small>
				</div>
				<div class="stand stand-south" class:occupied={view.position === STANDS.SOUTH}>
					<div class="magnet-field south-field"></div><div class="pad-disc"><span>S</span></div><p>South-up pad</p><small>−Z field</small>
				</div>

				<div class="urn-position position-{view.position}">
					<div class="carry-halo"></div>
					<div class="urn-lid"><div class="lid-fin left"></div><div class="lid-fin right"></div><div class="lid-cap"></div></div>
					<div class="urn-neck"></div>
					<div class="urn-body">
						<div class="soul-fill" style:height={`${Math.max(5, view.fillPercent)}%`}></div><div class="glass-shine"></div>
						<div class="soul-wisps" aria-hidden="true">{#each Array(7) as _, index}<span style={`--wisp:${index}`}></span>{/each}</div>
						<div class="urn-seal">NYC<br /><small>M.C.</small></div>
					</div>
					<div class="urn-foot"></div>
					<div class="neo-ring ring-top {view.state}">{#each pixelIndices as index}<span class:lit={fillPixelActive('top', index)} style={`--pixel-index:${index};--pixel-color:${pixelColor('top', index)}`}></span>{/each}</div>
					<div class="neo-ring ring-bottom {view.state}">{#each pixelIndices as index}<span class:lit={fillPixelActive('bottom', index)} style={`--pixel-index:${index};--pixel-color:${pixelColor('bottom', index)}`}></span>{/each}</div>
				</div>

				<div class="fill-meter">
					<div class="meter-label"><span>Soul capacity</span><strong>{view.fillPercent}%</strong></div>
					<div class="meter-track"><span style:width={`${view.fillPercent}%`}></span></div>
					<small>{view.state === STATES.FILLING ? `${Math.max(0, engine.fillTime - engine.fillElapsed).toFixed(1)}s remaining` : view.stateLabel}</small>
				</div>
			</div>

			<div class="audio-console">
				<div class="speaker-icon" class:active={Boolean(nowPlaying || ambientStage || (soundEnabled && view.state === STATES.CARRIED))}><span></span><i></i><i></i><i></i></div>
				<div class="now-playing">
					<p class="eyebrow">Artifact audio</p><strong>{nowPlaying ? nowPlaying.title : ambientStage ? 'Soul gathering ambience' : soundEnabled && view.state === STATES.CARRIED ? 'Urn footstep jingles' : view.powered ? 'Standing by' : 'Amplifier disabled'}</strong>
					<small>{nowPlaying ? titleCase(nowPlaying.group) : ambientStage ? ambientStage === 'loop' ? 'Filling loop' : 'Filling start' : soundEnabled && view.state === STATES.CARRIED ? 'Randomized running cadence' : audioLoaded ? `${audioAssetCount} converted mono assets indexed` : audioLoadError || 'Indexing audio…'}</small>
					<div class="audio-progress"><span style:width={`${audioProgress * 100}%`}></span></div>
				</div>
				<div class="audio-actions"><button class="icon-button" onclick={toggleSound}>{soundEnabled ? 'VOL' : 'MUTE'}</button><button class="icon-button" onclick={replayLast} disabled={!canReplayLast()}>REPLAY</button><button class="icon-button" onclick={skipAudio} disabled={!nowPlaying}>SKIP</button></div>
			</div>
		</div>

		<aside class="controls-column">
			<section class="control-panel primary-panel">
				<div class="panel-heading"><div><p class="eyebrow">Interaction deck</p><h3>Play with the urn</h3></div><span class="sensor-pill">TMAG + LIS3DH</span></div>
				<div class="control-group">
					<div class="group-label"><span>01</span><strong>Power & round</strong></div>
					<div class="button-grid two"><button class="action-button power-on" onclick={() => run(() => engine.powerOn())} disabled={view.powered}><span>Power on</span><small>Always start fresh</small></button><button class="action-button" onclick={powerOff} disabled={!view.powered}><span>Power off</span><small>Forget current round</small></button></div>
					<button class="action-button compact debug-button" onclick={() => run(() => engine.completeFill())} disabled={!view.chargingActive}><span>Instant fill</span><small>Debug · set charge to 100%</small></button>
					<button class="text-button danger-text" onclick={resetJourney}>Reset round & stage North</button>
				</div>
				<div class="control-group">
					<div class="group-label"><span>02</span><strong>Placement</strong></div>
					<div class="button-grid two">
						<button class:active={view.position === STANDS.NORTH} class="action-button" onclick={() => run(() => engine.placeOn(STANDS.NORTH))}><span>Place on North</span><small>North-up magnet</small></button>
						<button class:active={view.position === STANDS.SOUTH} class="action-button" onclick={() => run(() => engine.placeOn(STANDS.SOUTH))}><span>Place on South</span><small>South-up magnet</small></button>
						<button class:active={view.position === POSITIONS.CARRIED} class="action-button accent" onclick={() => run(() => engine.pickUp())} disabled={view.position === POSITIONS.CARRIED}><span>Pick up</span><small>Remove magnetic field</small></button>
						<button class:active={view.position === POSITIONS.GROUND} class="action-button" onclick={() => run(() => engine.gentleSetDown())} disabled={view.position !== POSITIONS.CARRIED}><span>Set down gently</span><small>Wait for stillness</small></button>
						<button class="action-button danger" onclick={() => run(() => engine.drop())} disabled={view.position !== POSITIONS.CARRIED}><span>Drop</span><small>Freefall → impact</small></button>
					</div>
				</div>
			</section>
		</aside>
	</section>
</main>
