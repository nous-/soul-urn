export const AUDIO_MATCHERS = Object.freeze({
	unhomed_prompt: [
		'/waiting/bridge/spirit_jar_idol_waiting_on_bridge_03.mp3',
		'/dropped_urn/spirit_jar_idol_dropped_05.mp3',
		'/dropped_urn/spirit_jar_idol_dropped_06.mp3'
	],
	wait_on_pad: '/waiting/bridge/',
	wait_off_pad: '/waiting/streets/',
	pickup_from_pad: '/picked_up_urn/bridge/',
	pickup_from_ground: '/picked_up_urn/streets/',
	pickup_effect: '/soul_urn_pickup/soul_urn_pickup_',
	dropoff_effect: '/soul_urn_dropoff/soul_urn_cashin_complete_',
	carrying: '/carrier_is_stalling/',
	carrying_jingle: '/carrying_jingles/footstep_urn_jingle_',
	hard_drop: '/dropped_urn/',
	delivered: '/delivered/',
	urn_expires: '/urn_expires/'
});

export function buildAudioGroups(files) {
	return Object.fromEntries(
		Object.entries(AUDIO_MATCHERS).map(([group, matcher]) => {
			const matchers = Array.isArray(matcher) ? matcher : [matcher];
			return [group, files.filter((path) => matchers.some((candidate) => path.includes(candidate)))];
		})
	);
}
