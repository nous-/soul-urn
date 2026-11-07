import assert from 'node:assert/strict';
import { PHASES, POSITIONS, STATES, STANDS, UrnEngine } from '../src/lib/urn-engine.js';

function audioEvents(engine) {
	return engine.drainEvents().flatMap((event) => event.audio);
}

const fullUrnAudio = [
	'wait_on_pad', 'wait_off_pad', 'pickup_from_pad', 'pickup_from_ground',
	'pickup_effect', 'dropoff_effect', 'carrying', 'hard_drop', 'delivered', 'urn_expires'
];

function assertConsistent(urn) {
	if (!urn.powered) {
		assert.equal(urn.state, STATES.POWERED_OFF);
		assert.equal(urn.phase, PHASES.EMPTY);
		assert.equal(urn.source, null);
		assert.equal(urn.fillElapsed, 0);
		return;
	}

	if (urn.state === STATES.UNHOMED) {
		assert.equal(urn.phase, PHASES.EMPTY);
		assert.equal(urn.source, null);
		assert.equal(urn.fillElapsed, 0);
		assert.ok(urn.position === POSITIONS.CARRIED || urn.position === POSITIONS.GROUND);
	}
	if (urn.state === STATES.FILLING) {
		assert.equal(urn.phase, PHASES.EMPTY);
		assert.equal(urn.position, urn.source);
		assert.ok(urn.fillElapsed < urn.fillTime);
	}
	if (urn.state === STATES.FILL_PAUSED) {
		assert.equal(urn.phase, PHASES.EMPTY);
		assert.ok(urn.source === STANDS.NORTH || urn.source === STANDS.SOUTH);
		assert.notEqual(urn.position, urn.source);
		assert.ok(urn.fillElapsed < urn.fillTime);
	}
	if (urn.state === STATES.REFILL_DELAY) {
		assert.equal(urn.phase, PHASES.EMPTY);
		assert.equal(urn.position, urn.source);
		assert.equal(urn.fillElapsed, 0);
		assert.equal(urn.chargingActive, false);
	}
	if (urn.state === STATES.READY) {
		assert.equal(urn.phase, PHASES.READY);
		assert.equal(urn.position, urn.source);
		assert.equal(urn.fillFraction, 1);
	}
	if (urn.state === STATES.CARRIED) {
		assert.equal(urn.phase, PHASES.TRANSIT);
		assert.ok(
			urn.position === POSITIONS.CARRIED
			|| (urn.position === POSITIONS.GROUND && urn.pendingSetDownAt !== null)
		);
	}
	if (urn.state === STATES.RESTING_WRONG) {
		assert.equal(urn.phase, PHASES.TRANSIT);
		assert.equal(urn.position, POSITIONS.GROUND);
	}
	if (urn.state === STATES.WRONG_SOURCE) {
		assert.equal(urn.phase, PHASES.TRANSIT);
		assert.equal(urn.position, urn.source);
	}
}

{
	const urn = new UrnEngine({ fillTime: 10, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	assert.equal(urn.state, STATES.FILLING);
	assert.equal(urn.source, STANDS.NORTH);
	urn.advance(10);
	assert.equal(urn.state, STATES.READY);
	urn.pickUp();
	assert.equal(urn.phase, PHASES.TRANSIT);
	assert.deepEqual(audioEvents(urn).slice(-2), ['pickup_effect', 'pickup_from_pad']);
	urn.suspendChatter();
	urn.advance(20);
	assert.equal(audioEvents(urn).includes('carrying'), false, 'no carried chatter while speech is playing');
	urn.resumeChatterAfterDialogue();
	urn.advance(9.9);
	assert.equal(audioEvents(urn).includes('carrying'), false);
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('carrying'), 'carried urn speaks every ten seconds');
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.state, STATES.WRONG_SOURCE);
	urn.pickUp();
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.source, STANDS.SOUTH);
	assert.equal(urn.state, STATES.REFILL_DELAY);
	assert.equal(urn.destination, STANDS.NORTH);
	urn.suspendRefillUntilDeliveryAudioEnds();
	urn.advance(20);
	assert.equal(urn.state, STATES.REFILL_DELAY, 'refill cannot begin while delivery audio is playing');
	urn.scheduleRefillAfterDeliveryAudio();
	urn.advance(4.9);
	assert.equal(urn.state, STATES.REFILL_DELAY);
	urn.advance(0.1);
	assert.equal(urn.state, STATES.FILLING, 'refill starts five seconds after delivered audio ends');
}

{
	const urn = new UrnEngine({ fillTime: 30, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	assert.deepEqual(audioEvents(urn), [], 'charging starts without spoken dialogue');
	urn.advance(29.9);
	assert.deepEqual(audioEvents(urn), [], 'Bridge dialogue never plays before the urn is full');
	urn.advance(0.2);
	assert.equal(urn.state, STATES.READY);
	urn.advance(10);
	assert.ok(audioEvents(urn).includes('wait_on_pad'), 'Bridge dialogue starts only after the urn is full');
}

{
	const early = new UrnEngine({ fillTime: 1, readyChatterDelay: 10, random: () => 0 });
	early.drainEvents();
	early.powerOn();
	early.advance(1);
	assert.equal(early.nextChatterAt - early.simTime, 8, 'recurring speech applies negative jitter');

	const late = new UrnEngine({ fillTime: 1, readyChatterDelay: 10, random: () => 1 });
	late.drainEvents();
	late.powerOn();
	late.advance(1);
	assert.ok(Math.abs((late.nextChatterAt - late.simTime) - 12) < 1e-9, 'recurring speech applies positive jitter');
}

{
	const urn = new UrnEngine({ fillTime: 1, readyChatterDelay: 10, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.drainEvents();
	urn.advance(1);
	assert.equal(urn.state, STATES.READY);
	urn.drainEvents();
	urn.advance(9.9);
	assert.equal(audioEvents(urn).includes('wait_on_pad'), false);
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('wait_on_pad'), 'full urn speaks about every ten seconds');
	urn.suspendChatter();
	urn.advance(30);
	assert.equal(audioEvents(urn).includes('wait_on_pad'), false);
	urn.resumeChatterAfterDialogue();
	urn.advance(10);
	assert.ok(audioEvents(urn).includes('wait_on_pad'), 'ready delay starts after the prior line ends');
}

{
	const urn = new UrnEngine({ fillTime: 2, setDownDelay: 1, random: () => 0.5 });
	urn.position = POSITIONS.CARRIED;
	urn.drainEvents();
	urn.powerOn();
	assert.equal(urn.state, STATES.UNHOMED);
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt'], 'powering on while held plays a contextual needs-pad prompt');
	urn.resumeChatterAfterDialogue();
	urn.advance(9.9);
	assert.deepEqual(audioEvents(urn), []);
	urn.advance(0.1);
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt'], 'the needs-pad prompt repeats about every ten seconds');
	urn.placeOn(STANDS.SOUTH);
	assert.deepEqual(audioEvents(urn), [], 'starting a fill has ambience but no full-urn dialogue');
	urn.advance(2);
	urn.pickUp();
	assert.ok(audioEvents(urn).includes('pickup_from_pad'));
	urn.gentleSetDown();
	assert.equal(urn.state, STATES.CARRIED);
	urn.advance(1);
	assert.equal(urn.state, STATES.RESTING_WRONG);
	assert.ok(audioEvents(urn).includes('wait_off_pad'));
	urn.pickUp();
	assert.deepEqual(
		audioEvents(urn),
		['pickup_from_ground'],
		'only the first lift from the source pad plays the pickup effect'
	);
}

{
	const urn = new UrnEngine({ fillTime: 1, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(1);
	urn.talkNow();
	assert.ok(audioEvents(urn).includes('wait_on_pad'));
	urn.pickUp();
	urn.drop();
	assert.equal(urn.state, STATES.RESTING_WRONG);
	assert.ok(audioEvents(urn).includes('hard_drop'));
	urn.talkNow();
	assert.ok(audioEvents(urn).includes('wait_off_pad'));
	urn.powerOff();
	assert.equal(urn.source, null);
	urn.powerOn();
	assert.equal(urn.state, STATES.UNHOMED);
	assert.equal(urn.source, null);
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt'], 'fresh off-pad power-on uses only the curated needs-pad pool');
}

{
	const urn = new UrnEngine({ fillTime: 1, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(1);
	urn.drainEvents();
	urn.drop();
	urn.gentleSetDown();
	assert.equal(urn.state, STATES.READY, 'motion buttons cannot move a full urn that was never picked up');
	assert.equal(urn.position, STANDS.NORTH);
	urn.placeOn(STANDS.SOUTH);
	const events = urn.drainEvents();
	assert.equal(urn.state, STATES.READY, 'direct destination placement cannot bypass pickup');
	assert.equal(urn.position, STANDS.NORTH);
	assert.ok(events.some((event) => event.type === 'pickup_required'));
	assert.equal(events.some((event) => event.type === 'delivery'), false);
}

{
	const urn = new UrnEngine({ fillTime: 10, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(4);
	urn.drainEvents();
	urn.pickUp();
	assert.equal(urn.state, STATES.FILL_PAUSED);
	assert.equal(urn.source, STANDS.NORTH);
	assert.equal(urn.fillElapsed, 4);
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt'], 'premature pickup immediately asks to be returned to a pad');
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.state, STATES.FILL_PAUSED);
	assert.equal(urn.fillElapsed, 4);
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt'], 'wrong pad before full immediately repeats the needs-pad prompt');
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.state, STATES.FILLING);
	assert.equal(urn.source, STANDS.NORTH);
	assert.equal(urn.fillElapsed, 4);
	urn.advance(6);
	assert.equal(urn.state, STATES.READY);
}

{
	const urn = new UrnEngine({ fillTime: 10, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(4);
	urn.pickUp();
	urn.gentleSetDown();
	urn.pickUp();
	assert.equal(urn.phase, PHASES.EMPTY, 'ground recovery cannot turn a partial fill into transit');
	assert.equal(urn.state, STATES.FILL_PAUSED);
	assert.equal(urn.fillElapsed, 4);
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.state, STATES.FILL_PAUSED);
	assert.equal(urn.source, STANDS.NORTH);
}

{
	const urn = new UrnEngine({ fillTime: 10, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(4);
	urn.drainEvents();
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.state, STATES.FILL_PAUSED, 'moving directly to the other pad cannot bypass filling');
	assert.equal(urn.source, STANDS.NORTH, 'source remains locked until full');
	assert.equal(urn.fillElapsed, 4, 'partial progress is preserved');
	assert.deepEqual(audioEvents(urn), ['unhomed_prompt']);
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.state, STATES.FILLING);
	assert.equal(urn.fillElapsed, 4);
	urn.advance(6);
	assert.equal(urn.state, STATES.READY);
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.state, STATES.READY, 'delivery cannot bypass the full-urn pickup transition');
	assert.equal(urn.source, STANDS.NORTH);
	urn.pickUp();
	assert.equal(urn.phase, PHASES.TRANSIT, 'only a full pickup starts delivery transit');
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.source, STANDS.SOUTH, 'delivery toggles the route only after full transit');
}

{
	const urn = new UrnEngine({ fillTime: 10, setDownDelay: 1, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	assert.equal(urn.chargingActive, true);
	urn.advance(10);
	assert.equal(urn.state, STATES.READY);
	assert.equal(urn.chargingActive, false);
	urn.pickUp();
	urn.drop();
	assert.equal(urn.phase, PHASES.TRANSIT);
	assert.equal(urn.chargingActive, false);
	urn.pickUp();
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.state, STATES.WRONG_SOURCE);
	urn.pickUp();
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.source, STANDS.SOUTH);
	assert.equal(urn.state, STATES.REFILL_DELAY);
	assert.equal(urn.chargingActive, false, 'delivery audio and cooldown happen before the next charge');
	urn.advance(5);
	assert.equal(urn.state, STATES.FILLING);
	assert.equal(urn.chargingActive, true, 'delivery pad becomes the next charging source after five seconds');
	urn.advance(4);
	urn.pickUp();
	assert.equal(urn.fillElapsed, 4);
	assert.equal(urn.chargingActive, false, 'charging stops immediately off the source pad');
	urn.advance(20);
	assert.equal(urn.fillElapsed, 4, 'off-pad time never increases charge');
	urn.gentleSetDown();
	urn.advance(2);
	assert.equal(urn.phase, PHASES.EMPTY);
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.state, STATES.FILL_PAUSED);
	urn.placeOn(STANDS.SOUTH);
	assert.equal(urn.chargingActive, true);
	assert.equal(urn.fillElapsed, 4);
	urn.advance(6);
	urn.pickUp();
	urn.placeOn(STANDS.NORTH);
	assert.equal(urn.source, STANDS.NORTH, 'second completed run toggles back to North');
	assert.equal(urn.state, STATES.REFILL_DELAY);
	assert.equal(urn.chargingActive, false);
	urn.advance(5);
	assert.equal(urn.chargingActive, true);
}

{
	const urn = new UrnEngine({ fillTime: 1, setDownDelay: 0, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(1);
	urn.pickUp();
	urn.drainEvents();
	urn.drop();
	assert.ok(audioEvents(urn).includes('hard_drop'));
	urn.resumeChatterAfterDialogue();
	urn.advance(4.9);
	assert.equal(audioEvents(urn).includes('wait_off_pad'), false);
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('wait_off_pad'), 'Streets dialogue repeats about five seconds after the prior line ends');
	urn.pickUp();
	urn.placeOn(STANDS.NORTH);
	assert.ok(audioEvents(urn).includes('wait_on_pad'));
	urn.resumeChatterAfterDialogue();
	urn.advance(9.9);
	assert.equal(audioEvents(urn).includes('wait_on_pad'), false);
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('wait_on_pad'), 'Bridge dialogue repeats about ten seconds after the prior line ends');
}

{
	const urn = new UrnEngine({ fillTime: 1, setDownDelay: 2.5, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(1);
	urn.pickUp();
	urn.drainEvents();
	urn.nextChatterAt = urn.simTime + 0.1;
	urn.gentleSetDown();
	urn.advance(2.4);
	assert.deepEqual(audioEvents(urn), [], 'the stillness window does not speak before a gentle set-down is confirmed');
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('wait_off_pad'), 'Streets dialogue begins when the set-down is confirmed');
}

{
	const urn = new UrnEngine({ fillTime: 1, deliveryExpiry: 180, random: () => 0.5 });
	urn.drainEvents();
	urn.powerOn();
	urn.advance(1);
	urn.pickUp();
	urn.drainEvents();
	urn.advance(179.9);
	assert.equal(audioEvents(urn).includes('urn_expires'), false);
	urn.advance(0.1);
	assert.ok(audioEvents(urn).includes('urn_expires'), 'expiry line plays once after three minutes in transit');
	urn.advance(180);
	assert.equal(audioEvents(urn).includes('urn_expires'), false, 'expiry line does not repeat');
	assert.equal(urn.phase, PHASES.TRANSIT, 'expiry voice does not invent an unspecified reset');
}

{
	const urn = new UrnEngine();
	const ids = [];
	ids.push(...urn.drainEvents().map((event) => event.id));
	urn.powerOn();
	ids.push(...urn.drainEvents().map((event) => event.id));
	urn.powerOff();
	ids.push(...urn.drainEvents().map((event) => event.id));
	urn.powerOn();
	ids.push(...urn.drainEvents().map((event) => event.id));
	assert.equal(new Set(ids).size, ids.length, 'event IDs stay unique without advancing time');
}

{
	const urn = new UrnEngine({ fillTime: 3, setDownDelay: 0.25, random: () => 0.5 });
	const actions = [
		() => urn.powerOn(),
		() => urn.powerOff(),
		() => urn.placeOn(STANDS.NORTH),
		() => urn.placeOn(STANDS.SOUTH),
		() => urn.pickUp(),
		() => urn.gentleSetDown(),
		() => urn.drop(),
		() => urn.completeFill(),
		() => urn.advance(0.25),
		() => urn.advance(1),
		() => urn.advance(5)
	];
	let seed = 0x5eed1234;
	for (let index = 0; index < 5000; index += 1) {
		seed = (seed * 1664525 + 1013904223) >>> 0;
		actions[seed % actions.length]();
		assertConsistent(urn);
		for (const event of urn.drainEvents()) {
			if (!urn.isFull && event.type !== 'delivery') {
				assert.equal(event.audio.some((group) => fullUrnAudio.includes(group)), false);
			}
		}
	}
}

{
	const urn = new UrnEngine();
	urn.position = POSITIONS.CARRIED;
	urn.drainEvents();
	urn.powerOn();
	urn.drainEvents();
	urn.emit('guard_probe', 'Invalid full-urn audio must be rejected.', fullUrnAudio);
	assert.deepEqual(audioEvents(urn), [], 'the engine rejects every full-urn audio group while empty');
}

console.log('Urn engine scenarios passed.');
