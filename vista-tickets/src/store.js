// Armazenamento simples em JSON (data/tickets.json)
const fs = require('node:fs');
const path = require('node:path');

const DIR = path.join(__dirname, '..', 'data');
const FILE = path.join(DIR, 'tickets.json');

let db = { counter: 0, tickets: {} };

function load() {
  try {
    db = JSON.parse(fs.readFileSync(FILE, 'utf8'));
    db.counter ??= 0;
    db.tickets ??= {};
  } catch {
    db = { counter: 0, tickets: {} };
  }
}

function save() {
  fs.mkdirSync(DIR, { recursive: true });
  const tmp = FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(db, null, 2));
  fs.renameSync(tmp, FILE);
}

function nextNumber() {
  db.counter += 1;
  save();
  return db.counter;
}

function create(channelId, data) {
  db.tickets[channelId] = data;
  save();
}

function get(channelId) {
  return db.tickets[channelId] ?? null;
}

function update(channelId, patch) {
  if (!db.tickets[channelId]) return;
  Object.assign(db.tickets[channelId], patch);
  save();
}

function remove(channelId) {
  delete db.tickets[channelId];
  save();
}

function openByUser(guildId, userId) {
  return Object.entries(db.tickets).find(
    ([, t]) => t.guildId === guildId && t.ownerId === userId,
  );
}

load();
module.exports = { nextNumber, create, get, update, remove, openByUser };
