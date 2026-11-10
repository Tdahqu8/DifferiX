---
id: roblox_luau
name: Luau (Roblox Scripting)
extensions: [.luau, .lua]
---

# RESEARCHER
- Plan modular structures split clearly between ServerScriptService, ReplicatedStorage, and StarterPlayerScripts.
- Always utilize game:GetService("ServiceName") patterns for engine services.

# EDITOR
- Use strict Luau type annotations (e.g., local player: Player).
- Use task.wait(), task.spawn(), and task.defer() instead of legacy wait() or coroutines.
- Maintain strict network boundaries over RemoteEvent and RemoteFunction channels.

# REVIEWER
- Verify script placement (Server vs Client), check for deprecated wait() calls, and validate RemoteEvent arguments.