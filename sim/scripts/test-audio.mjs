import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { buildAudioGroups } from '../src/lib/audio-catalog.js';

const audioRoot = new URL('../static/audio/', import.meta.url);
const manifest = JSON.parse(readFileSync(new URL('manifest.json', audioRoot), 'utf8'));
const diskFiles = readdirSync(audioRoot, { recursive: true, withFileTypes: true })
	.filter((entry) => entry.isFile() && entry.name.endsWith('.mp3'))
	.map((entry) => {
		const absolute = join(entry.parentPath, entry.name);
		return `/audio/${relative(audioRoot.pathname, absolute).split(sep).join('/')}`;
	})
	.sort();

assert.equal(new Set(manifest.files).size, manifest.files.length, 'audio manifest paths are unique');
assert.deepEqual([...manifest.files].sort(), diskFiles, 'audio manifest exactly matches the MP3 files on disk');

const groups = buildAudioGroups(manifest.files);
for (const [group, files] of Object.entries(groups)) {
	assert.ok(files.length > 0, `${group} has at least one playable file`);
}

const expectedCounts = {
	unhomed_prompt: 3,
	wait_on_pad: 17,
	wait_off_pad: 10,
	pickup_from_pad: 3,
	pickup_from_ground: 8,
	pickup_effect: 3,
	dropoff_effect: 2,
	carrying: 5,
	carrying_jingle: 10,
	hard_drop: 10,
	delivered: 10,
	urn_expires: 12
};
for (const [group, count] of Object.entries(expectedCounts)) {
	assert.equal(groups[group].length, count, `${group} contains the expected categorized assets`);
}

console.log(`Audio catalog passed (${manifest.files.length} MP3 files).`);
