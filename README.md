# TimeTree Calendar Context

Small cloud-runner repo for exporting a TimeTree calendar to ICS, converting it into compact planner-facing JSON, and posting it to n8n.

The intended flow is:

1. GitHub Actions runs `timetree-exporter` on a schedule.
2. `scripts/timetree_calendar_context.py` converts the exported ICS into calendar-load JSON.
3. The workflow posts that JSON to an n8n webhook.
4. n8n stores the latest successful payload for the Morning Planner.

## Required GitHub Secrets

- `TIMETREE_EMAIL`
- `TIMETREE_PASSWORD`
- `TIMETREE_CALENDAR_CODE`
- `N8N_TIMETREE_CONTEXT_WEBHOOK_URL`
- `N8N_TIMETREE_CONTEXT_API_KEY`

## Local Conversion

If Python dependencies are installed locally, convert an exported ICS file with:

```bash
python scripts/timetree_calendar_context.py --ics timetree.ics --timezone Europe/London --output calendar-context.json
```

The default lookahead window is 14 days. Override it with:

```bash
python scripts/timetree_calendar_context.py --ics timetree.ics --lookahead-days 30
```

## Notes

- TimeTree has no official public API for this use case; `timetree-exporter` uses unofficial web APIs.
- TimeTree label categories and colours are preserved as `labels` and `color` in event payloads when present in the exported ICS.
- Known household colours are mapped to `participation`, `involves`, and `affects_capacity`; Eleanor-only plans are kept as context but do not reduce Oliver's availability, while birthday events are discarded.
- Unknown colours are retained and treated as capacity-affecting until mapped.
- Avoid fetching comments for the scheduled planner path unless needed, because that adds extra TimeTree requests.
- The JSON payload is a compact planning summary, not a full calendar archive.
