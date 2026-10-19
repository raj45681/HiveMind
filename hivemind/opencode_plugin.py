"""Generate a dependency-free OpenCode plugin using documented message events."""
import json
from pathlib import Path
import sys


def plugin_source(root, python=None):
    command = [str(python or sys.executable), str(Path(__file__).resolve().parents[1] / 'hive.py'),
               '--root', str(Path(root).resolve()), 'assist-hook', '--agent', 'opencode']
    return '''// HiveMind managed activity adapter. No model calls in the agent process.
import { spawn } from "node:child_process";
const command = COMMAND;
export const HiveMind = async ({ directory, worktree }) => {
  const sessions = new Map();
  const send = (payload) => new Promise((resolve) => {
    const child = spawn(command[0], command.slice(1), { stdio: ["pipe", "ignore", "ignore"], windowsHide: true });
    const timer = setTimeout(() => { child.kill(); resolve(); }, 4000);
    child.on("error", () => { clearTimeout(timer); resolve(); });
    child.on("close", () => { clearTimeout(timer); resolve(); });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(payload));
  });
  return {
    event: async ({ event }) => {
      const p = event.properties || {};
      const info = p.info || p.part || {};
      const sid = info.sessionID || p.sessionID;
      if (!sid) return;
      if (!sessions.has(sid)) {
        if (sessions.size >= 8) sessions.delete(sessions.keys().next().value);
        sessions.set(sid, new Map());
      }
      const messages = sessions.get(sid);
      if (event.type === "message.updated" || event.type === "message.part.updated") {
        const id = event.type === "message.updated" ? info.id : info.messageID;
        if (!id) return;
        if (!messages.has(id)) {
          if (messages.size >= 16) messages.delete(messages.keys().next().value);
          messages.set(id, { role: null, parts: new Map() });
        }
        const message = messages.get(id);
        if (event.type === "message.updated") message.role = info.role;
        // Deliberately exclude reasoning, attachments, tool payloads and synthetic text.
        if (event.type === "message.part.updated" && info.type === "text" && !info.synthetic && !info.ignored)
          message.parts.set(info.id, String(info.text || "").slice(0, 8000));
        return;
      }
      if (!["session.idle", "session.error", "session.compacted", "session.deleted"].includes(event.type)) return;
      const events = [];
      for (const [id, message] of messages) {
        if (!["user", "assistant"].includes(message.role)) continue;
        const text = [...message.parts.values()].join("\\n").slice(0, 8000);
        if (text.trim()) events.push({ id, role: message.role, text });
      }
      sessions.delete(sid);
      // One process per completed turn, never one process or model call per token.
      if (events.length) await send({ hook_event_name: "Batch", cwd: directory || worktree, session_id: sid, events });
    }
  };
};
'''.replace('COMMAND', json.dumps(command))
