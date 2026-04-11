---
name: printer-send
description: "Choke-point wrapper for sending G-code to printers. Validates against printer_policy.yaml, checks state, logs, enforces allowlist. Prefer this over raw curl commands."
user-invocable: true
disable-model-invocation: false
---

# /printer-send — safe G-code dispatch

This skill is a CHOKE POINT for printer commands. Instead of constructing raw curl commands (which the printer_safety.py hook must then regex-parse), use this skill to send G-code safely.

## Usage

/printer-send <gcode> [--printer <id>] [--force-check]

Examples:
- /printer-send M117 Hello World
- /printer-send M220 S110
- /printer-send PAUSE

## How it works

1. Query printer state via Moonraker API
2. Validate the G-code against the policy in ~/.claude/policies/printer_policy.yaml
3. If allowed: send via Moonraker API, log to audit trail
4. If blocked: refuse with reason, log the denial

## Instructions

When invoked:

1. Parse the gcode from $ARGUMENTS
2. Determine the target printer (default: Sovol SV08 Max at 192.168.0.108)
3. Query print state: curl -s http://<ip>:7125/printer/objects/query?print_stats
4. Check the gcode against the policy:
   - If in always_blocked: REFUSE regardless of state
   - If state is printing/paused and gcode is NOT in allowed_during_print: REFUSE
   - If state is unknown: only allow if gcode is in allowed_during_print
5. If allowed: send via curl -X POST http://<ip>:7125/printer/gcode/script -d "script=<gcode>"
6. Log the action to ~/.claude/printer_audit.log
7. Report the result to Tim

## Important

- This skill does NOT bypass the printer_safety.py hook — the hook remains as a backstop
- The hook catches raw curl commands; this skill is the PREFERRED path
- Always prefer /printer-send over constructing your own curl commands
- The policy file is the single source of truth for what is allowed
