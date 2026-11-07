export const STANDS = Object.freeze({ NORTH: 'north', SOUTH: 'south' });
export const POSITIONS = Object.freeze({
	NORTH: 'north',
	SOUTH: 'south',
	CARRIED: 'carried',
	GROUND: 'ground'
});

export const PHASES = Object.freeze({
	EMPTY: 'empty',
	READY: 'ready',
	TRANSIT: 'transit'
});

export const CHARGE_SOUND_DURATION_SECONDS = 15.777959;

export const STATES = Object.freeze({
	POWERED_OFF: 'powered_off',
	UNHOMED: 'unhomed',
	FILLING: 'filling',
	FILL_PAUSED: 'fill_paused',
	REFILL_DELAY: 'refill_delay',
	READY: 'ready',
	CARRIED: 'carried',
	RESTING_WRONG: 'resting_wrong',
	WRONG_SOURCE: 'wrong_source'
});

export function oppositeStand(stand) {
	if (stand === STANDS.NORTH) return STANDS.SOUTH;
	if (stand === STANDS.SOUTH) return STANDS.NORTH;
	return null;
}

const labels = {
	[STATES.POWERED_OFF]: 'Powered off',
	[STATES.UNHOMED]: 'Empty · needs a stand',
	[STATES.FILLING]: 'Gathering souls',
	[STATES.FILL_PAUSED]: 'Gathering paused · return to source',
	[STATES.REFILL_DELAY]: 'Delivered · refill begins shortly',
	[STATES.READY]: 'Full · awaiting pickup',
	[STATES.CARRIED]: 'In transit',
	[STATES.RESTING_WRONG]: 'Left off-pad',
	[STATES.WRONG_SOURCE]: 'Returned to source pad'
};

const FULL_URN_AUDIO_GROUPS = new Set([
	'wait_on_pad',
	'wait_off_pad',
	'pickup_from_pad',
	'pickup_from_ground',
	'pickup_effect',
	'dropoff_effect',
	'carrying',
	'hard_drop',
	'delivered',
	'urn_expires'
]);

export class UrnEngine {
	constructor(options = {}) {
		this.random = options.random ?? Math.random;
		this.fillTime = options.fillTime ?? CHARGE_SOUND_DURATION_SECONDS;
		this.carryingChatterDelay = options.carryingChatterDelay ?? 10;
		this.streetsChatterDelay = options.streetsChatterDelay ?? 5;
		this.unhomedChatterDelay = options.unhomedChatterDelay ?? 10;
		this.readyChatterDelay = options.readyChatterDelay ?? 10;
		this.deliveryExpiry = options.deliveryExpiry ?? 180;
		this.refillDelay = options.refillDelay ?? 5;
		this.setDownDelay = options.setDownDelay ?? 2.5;
		this.eventSequence = 0;
		this.reset();
	}

	reset() {
		this.powered = false;
		this.state = STATES.POWERED_OFF;
		this.position = POSITIONS.NORTH;
		this.source = null;
		this.phase = PHASES.EMPTY;
		this.fillElapsed = 0;
		this.simTime = 0;
		this.nextChatterAt = Infinity;
		this.pendingSetDownAt = null;
		this.deliveryElapsed = 0;
		this.expirySpoken = false;
		this.refillAt = null;
		this.events = [];
		this.emit('reset', 'Round cleared. Urn staged on the North pad.');
	}

	get destination() {
		return oppositeStand(this.source);
	}

	get fillFraction() {
		if (this.state === STATES.READY || this.phase === PHASES.READY || this.phase === PHASES.TRANSIT) {
			return 1;
		}
		if ((this.state !== STATES.FILLING && this.state !== STATES.FILL_PAUSED) || this.fillTime <= 0) return 0;
		return Math.min(1, this.fillElapsed / this.fillTime);
	}

	get isFull() {
		return this.phase === PHASES.READY || this.phase === PHASES.TRANSIT;
	}

	get chargingActive() {
		return this.powered
			&& this.state === STATES.FILLING
			&& this.source !== null
			&& this.position === this.source;
	}

	get stateLabel() {
		return labels[this.state] ?? this.state;
	}

	configure({ fillTime, setDownDelay }) {
		if (Number.isFinite(fillTime)) this.fillTime = Math.max(1, fillTime);
		if (Number.isFinite(setDownDelay)) this.setDownDelay = Math.max(0, setDownDelay);
	}

	emit(type, message, audio = [], tone = 'neutral') {
		const allowedAudio = audio.filter((group) => !FULL_URN_AUDIO_GROUPS.has(group) || this.isFull);
		this.events.push({
			id: `${this.simTime.toFixed(3)}-${this.eventSequence++}-${type}`,
			time: this.simTime,
			type,
			message,
			audio: allowedAudio,
			tone
		});
	}

	drainEvents() {
		const drained = this.events;
		this.events = [];
		return drained;
	}

	scheduleChatter() {
		if (this.state === STATES.FILLING) {
			this.nextChatterAt = Infinity;
			return;
		}
		let baseDelay;
		if (this.state === STATES.CARRIED) baseDelay = this.carryingChatterDelay;
		else if (this.state === STATES.RESTING_WRONG) baseDelay = this.streetsChatterDelay;
		else if (this.state === STATES.UNHOMED || this.state === STATES.FILL_PAUSED) baseDelay = this.unhomedChatterDelay;
		else if (this.state === STATES.READY || this.state === STATES.WRONG_SOURCE) baseDelay = this.readyChatterDelay;
		else {
			this.nextChatterAt = Infinity;
			return;
		}
		const jitter = 0.8 + this.random() * 0.4;
		this.nextChatterAt = this.simTime + baseDelay * jitter;
	}

	suspendChatter() {
		this.nextChatterAt = Infinity;
	}

	resumeChatterAfterDialogue() {
		if (!this.powered) return;
		this.scheduleChatter();
	}

	powerOn() {
		if (this.powered) return;
		this.powered = true;
		this.source = null;
		this.phase = PHASES.EMPTY;
		this.fillElapsed = 0;
		this.pendingSetDownAt = null;
		this.nextChatterAt = Infinity;
		this.deliveryElapsed = 0;
		this.expirySpoken = false;
		this.refillAt = null;
		this.emit('power', 'Power on. Starting a completely fresh round.', [], 'system');

		const onStand = this.position === POSITIONS.NORTH || this.position === POSITIONS.SOUTH;
		const presentStand = onStand ? this.position : null;
		if (presentStand) this.beginFilling(presentStand, 'This pad becomes the new source.');
		else this.setUnhomed('Powered on off-pad. Place the empty urn on either pad.');
	}

	powerOff() {
		if (!this.powered) return;
		this.powered = false;
		this.state = STATES.POWERED_OFF;
		this.source = null;
		this.phase = PHASES.EMPTY;
		this.fillElapsed = 0;
		this.pendingSetDownAt = null;
		this.nextChatterAt = Infinity;
		this.deliveryElapsed = 0;
		this.expirySpoken = false;
		this.refillAt = null;
		this.emit('power', 'Power off. The current round is forgotten; audio and lights stop.', [], 'system');
	}

	setUnhomed(message) {
		this.state = STATES.UNHOMED;
		this.phase = PHASES.EMPTY;
		this.fillElapsed = 0;
		this.scheduleChatter();
		this.emit('unhomed', message, ['unhomed_prompt'], 'warning');
	}

	beginFilling(stand, message = 'Soul gathering begins.') {
		this.source = stand;
		this.phase = PHASES.EMPTY;
		this.state = STATES.FILLING;
		this.fillElapsed = 0;
		this.pendingSetDownAt = null;
		this.deliveryElapsed = 0;
		this.expirySpoken = false;
		this.refillAt = null;
		this.nextChatterAt = Infinity;
		this.emit('fill_start', `${message} Source: ${stand}.`, [], 'soul');
	}

	resumeFilling() {
		this.phase = PHASES.EMPTY;
		this.state = STATES.FILLING;
		this.pendingSetDownAt = null;
		this.nextChatterAt = Infinity;
		this.emit(
			'fill_resume',
			`Soul gathering resumed at ${Math.round(this.fillFraction * 100)}%.`,
			[],
			'soul'
		);
	}

	completeFill() {
		if (!this.powered || this.state !== STATES.FILLING || this.phase !== PHASES.EMPTY || !this.source || this.position !== this.source) return;
		this.fillElapsed = this.fillTime;
		this.phase = PHASES.READY;
		this.state = STATES.READY;
		this.scheduleChatter();
		this.emit('full', `Urn full. Deliver it to the ${this.destination} pad.`, [], 'success');
	}

	placeOn(stand) {
		if (stand !== STANDS.NORTH && stand !== STANDS.SOUTH) return;
		if (this.powered && this.phase === PHASES.READY && stand === this.destination) {
			this.emit('pickup_required', 'The urn is full, but it must be picked up before delivery.', [], 'warning');
			return;
		}
		this.position = stand;
		this.pendingSetDownAt = null;
		this.emit('placement', `Placed on the ${stand} pad.`, [], 'physical');
		if (!this.powered) return;

		if (this.phase === PHASES.EMPTY) {
			if (!this.source) {
				this.beginFilling(stand, 'This pad is now the source.');
				return;
			}

			if (stand !== this.source) {
				this.refillAt = null;
				this.state = STATES.FILL_PAUSED;
				this.scheduleChatter();
				this.emit(
					'wrong_pad',
					`Not full yet. Return the urn to the ${this.source} pad to continue filling.`,
					['unhomed_prompt'],
					'warning'
				);
				return;
			}

			if (this.state === STATES.REFILL_DELAY) return;
			if (this.state !== STATES.FILLING) this.resumeFilling();
			return;
		}

		if (this.phase === PHASES.TRANSIT) {
			if (stand === this.destination) this.deliver(stand);
			else if (stand === this.source) this.setWrongSource(true);
			return;
		}

		if (this.phase === PHASES.READY) {
			this.state = STATES.READY;
			this.scheduleChatter();
			return;
		}

	}

	pickUp() {
		const previousPosition = this.position;
		if (previousPosition === POSITIONS.CARRIED) return;
		this.position = POSITIONS.CARRIED;
		this.pendingSetDownAt = null;
		this.emit('placement', 'Urn lifted.', [], 'physical');
		if (!this.powered) return;

		if (this.phase === PHASES.EMPTY) {
			if (!this.source) {
				this.setUnhomed('Off-pad and no source selected. Place the urn on either pad.');
				return;
			}
			this.state = STATES.FILL_PAUSED;
			this.refillAt = null;
			this.scheduleChatter();
			this.emit(
				'fill_paused',
				`Charging paused at ${Math.round(this.fillFraction * 100)}%. Return it to the ${this.source} pad.`,
				['unhomed_prompt'],
				'warning'
			);
			return;
		}

		if (this.phase === PHASES.READY) {
			this.phase = PHASES.TRANSIT;
			this.state = STATES.CARRIED;
			this.deliveryElapsed = 0;
			this.expirySpoken = false;
			this.scheduleChatter();
			this.emit(
				'pickup',
				`Pickup confirmed. Destination: ${this.destination}.`,
				['pickup_effect', 'pickup_from_pad'],
				'success'
			);
			return;
		}

		if (this.phase === PHASES.TRANSIT) {
			const recoveredFromGround = previousPosition === POSITIONS.GROUND || this.state === STATES.RESTING_WRONG;
			this.state = STATES.CARRIED;
			this.scheduleChatter();
			this.emit(
				'pickup',
				recoveredFromGround
					? `Off-pad recovery. Continue to the ${this.destination} pad.`
					: `Journey resumed. The ${this.destination} pad is still required.`,
				// Only the first lift from the source pad plays the pickup effect.
				[recoveredFromGround ? 'pickup_from_ground' : 'pickup_from_pad'],
				'soul'
			);
		}
	}

	gentleSetDown() {
		if (this.position !== POSITIONS.CARRIED) return;
		this.position = POSITIONS.GROUND;
		this.emit('placement', 'Set gently on an unmarked surface.', [], 'physical');
		if (!this.powered || this.phase !== PHASES.TRANSIT) return;
		this.nextChatterAt = Infinity;
		this.pendingSetDownAt = this.simTime + this.setDownDelay;
		this.emit(
			'settle_pending',
			`Waiting ${this.setDownDelay.toFixed(1)}s for stillness confirmation.`,
			[],
			'system'
		);
	}

	drop() {
		if (this.position !== POSITIONS.CARRIED) return;
		this.position = POSITIONS.GROUND;
		this.pendingSetDownAt = null;
		this.emit('drop', 'Freefall followed by impact detected.', [], 'danger');
		if (!this.powered || this.phase !== PHASES.TRANSIT) return;
		this.state = STATES.RESTING_WRONG;
		this.scheduleChatter();
		this.emit('hard_drop', 'Hard drop: red flash and stunned response.', ['hard_drop'], 'danger');
	}

	setWrongSource(speak) {
		this.state = STATES.WRONG_SOURCE;
		this.scheduleChatter();
		this.emit(
			'wrong_source',
			`Rejected: return trips do not count. Use the ${this.destination} pad.`,
			speak ? ['wait_on_pad'] : [],
			'warning'
		);
	}

	deliver(destination, boot = false) {
		if (!this.powered || this.phase !== PHASES.TRANSIT || destination !== this.destination || this.position !== destination) return;
		this.emit(
			'delivery',
			`Delivery accepted at the ${destination} pad${boot ? ' during boot recovery' : ''}.`,
			['dropoff_effect', 'delivered'],
			'success'
		);
		this.source = destination;
		this.phase = PHASES.EMPTY;
		this.state = STATES.REFILL_DELAY;
		this.fillElapsed = 0;
		this.pendingSetDownAt = null;
		this.deliveryElapsed = 0;
		this.expirySpoken = false;
		this.refillAt = this.simTime + this.refillDelay;
		this.nextChatterAt = Infinity;
		this.emit('route_toggle', `Route reversed. Next destination: ${this.destination}.`, [], 'system');
		this.emit('refill_pending', 'Waiting for the delivery audio and refill delay.', [], 'system');
	}

	suspendRefillUntilDeliveryAudioEnds() {
		if (this.state === STATES.REFILL_DELAY) this.refillAt = Infinity;
	}

	scheduleRefillAfterDeliveryAudio() {
		if (this.state === STATES.REFILL_DELAY) this.refillAt = this.simTime + this.refillDelay;
	}

	startRefill() {
		if (this.state !== STATES.REFILL_DELAY || this.position !== this.source) return;
		this.state = STATES.FILLING;
		this.refillAt = null;
		this.nextChatterAt = Infinity;
		this.emit('fill_start', `Soul gathering begins on the ${this.source} pad.`, [], 'soul');
	}

	talkNow() {
		if (!this.powered) return;
		if (this.state === STATES.FILLING) {
			this.nextChatterAt = Infinity;
			return;
		}
		const onPad = this.position === POSITIONS.NORTH || this.position === POSITIONS.SOUTH;
		const category = this.state === STATES.UNHOMED || this.state === STATES.FILL_PAUSED
			? 'unhomed_prompt'
			: onPad && (this.state === STATES.READY || this.state === STATES.WRONG_SOURCE)
			? 'wait_on_pad'
			: this.position === POSITIONS.CARRIED && this.phase === PHASES.TRANSIT
				? 'carrying'
				: this.position === POSITIONS.GROUND
					? 'wait_off_pad'
					: null;

		if (category) this.emit('chatter', `Contextual dialogue: ${category}.`, [category], 'voice');
		else this.emit('chatter', 'No dialogue is valid in this state.', [], 'system');
		this.scheduleChatter();
	}

	advance(seconds) {
		if (!Number.isFinite(seconds) || seconds <= 0) return;
		const elapsedSeconds = seconds;
		const targetTime = this.simTime + seconds;

		if (this.powered && this.state === STATES.REFILL_DELAY && Number.isFinite(this.refillAt) && this.refillAt <= targetTime) {
			const untilRefill = Math.max(0, this.refillAt - this.simTime);
			this.simTime += untilRefill;
			seconds -= untilRefill;
			this.startRefill();
		}

		while (this.chargingActive && this.fillElapsed < this.fillTime) {
			const remainingFill = this.fillTime - this.fillElapsed;
			if (remainingFill > seconds) break;
			this.fillElapsed = this.fillTime;
			this.simTime += remainingFill;
			seconds -= remainingFill;
			this.completeFill();
			break;
		}

		if (this.chargingActive) {
			this.fillElapsed = Math.min(this.fillTime, this.fillElapsed + seconds);
		}
		this.simTime = targetTime;

		if (this.powered && this.phase === PHASES.TRANSIT) {
			this.deliveryElapsed += elapsedSeconds;
			if (!this.expirySpoken && this.deliveryElapsed >= this.deliveryExpiry) {
				this.expirySpoken = true;
				this.scheduleChatter();
				this.emit(
					'urn_expired',
					`The delivery has taken ${Math.round(this.deliveryElapsed)} seconds.`,
					['urn_expires'],
					'warning'
				);
			}
		}

		if (this.powered && this.pendingSetDownAt !== null && this.simTime >= this.pendingSetDownAt) {
			this.pendingSetDownAt = null;
			if (this.position === POSITIONS.GROUND && this.phase === PHASES.TRANSIT) {
				this.state = STATES.RESTING_WRONG;
				this.scheduleChatter();
				this.emit(
					'gentle_setdown',
					'Stillness confirmed off-pad. This is a set-down, not a hard drop.',
					['wait_off_pad'],
					'warning'
				);
			}
		}

		if (this.powered && this.simTime >= this.nextChatterAt) this.talkNow();
	}
}
