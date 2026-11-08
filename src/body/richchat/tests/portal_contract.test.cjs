'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'chat.js'), 'utf8');
const portal = fs.readFileSync(path.join(root, '..', 'UdpListener.RichChatPortal.cs'), 'utf8');

test('RichChat must have the workspace navigation element expected by the runtime', () => {
  assert.match(html, /<button\b[^>]*id="workspaceBtn"[^>]*>/);
  assert.match(html, /id="workspaceBtn"[^>]*aria-pressed="false"/);
  assert.match(script, /workspaceBtn\?\.addEventListener\('click'/);
  assert.match(script, /\bpoll\(\);setInterval/);
});

test('user-visible editor does not promise an unimplemented artifact save', () => {
  assert.match(portal, /artifact_edit_not_implemented/);
  assert.doesNotMatch(html, /Chỉnh sửa artifact/);
  assert.doesNotMatch(script, /ErisWorkspaceEditor\.open/);
  assert.match(script, /Nhờ Eris sửa/);
});

test('rich chat assets are bundled for the built Windows app', () => {
  const project = fs.readFileSync(path.join(root, '..', '..', '..', 'Eris.csproj'), 'utf8');
  assert.match(project, /EmbeddedResource Include="src\/body\/richchat\/\*\*\/\*"/);
  for (const name of ['index.html', 'workspace.css', 'chat.css', 'chat.js', 'artifacts.js', 'canvas.html', 'canvas.css']) {
    assert.ok(portal.includes(`"${name}"`), `Missing embedded asset: ${name}`);
  }
});
