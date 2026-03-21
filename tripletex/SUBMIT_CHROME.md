# Chrome Submit — For Claude with Browser Control

You are a Claude session with Chrome browser access. Your ONLY job is to submit to the NM i AI competition.

## Setup

- Tab is open at: `https://app.ainm.no/submit/tripletex`
- Endpoint URL: `https://plot-freedom-aquarium-franklin.trycloudflare.com/solve`

## Steps (repeat in a loop)

1. Read the page to find the form elements (use `read_page` with `filter: interactive`)
2. Set the Endpoint URL input to the tunnel URL above (use `form_input`)
3. Click the Submit button (use `left_click` on the submit button ref)
4. Wait 60 seconds (use bash `sleep 60`)
5. Refresh the page (press F5)
6. Take a screenshot to see the latest result
7. Report the score from "Recent Results" (top entry)
8. Go back to step 1

## Rules

- NEVER write files or edit code
- NEVER run bash commands other than `sleep`
- Just submit, wait, report score, repeat
- If the endpoint URL is already filled, skip step 2
- Stop after 20 submissions or if the user tells you to stop
