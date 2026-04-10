# Printer Safety Rules

## MANDATORY: Check State First
Before sending ANY printer command, check `print_stats.state` first.

## ALLOWLIST — Commands Permitted During Printing/Paused
When print_stats.state == "printing" OR "paused", ONLY these commands are allowed:
- `M117` — display message
- `SET_GCODE_OFFSET` — Z offset adjustment
- `M220` — speed factor (50-150%)
- `M221` — flow rate (80-120%)
- `SET_FAN_SPEED` — fan control
- `PAUSE` / `RESUME` — pause/resume print
- `CANCEL_PRINT_CONFIRMED` — cancel print (NOT `CANCEL_PRINT`)

**Any command NOT on this list during a print = ask Tim first. No exceptions.**
A PreToolUse hook enforces this allowlist technically — blocked commands return a deny decision.

## FIRMWARE_RESTART / RESTART
NEVER send without explicit permission from Tim — regardless of printer state, even after a print finishes.

## TECHNICAL ENFORCEMENT
- `SAVE_CONFIG` Klipper macro blocks itself if print is active
- PreToolUse hook (`~/.claude/hooks/printer-safety-check.sh`) enforces allowlist via deny decision
- All printer commands are logged to `~/.claude/printer_audit.log`

## UPS WATCHDOG
**PERMANENTLY DELETED 2026-03-12.** Do NOT recreate it.
