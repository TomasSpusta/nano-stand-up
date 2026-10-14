# Later ideas

Ideas agreed during design, but kept out of the first version.
Move each one to a GitHub issue when work on it starts.

## Needed before real use

- **Service account for the booking API.**
  Testing uses the service admin's personal API key.
  Real use needs a separate account with read-only access to CF nano instruments, reservations, experiment plans, and certificates.
- **Access to the university mail server.**
  Testing uses Mailpit, a local mail catcher, so no email leaves the machine.
- **A CEITEC or VUT server** to run the sign-up page and the Monday job.

## Features

- **Alternative owners as guarantors.**
  The first version treats only the instrument owner as the guarantor.
  Later, the instrument's alternative owners can subscribe too.
- **One email per guarantor.**
  The first version sends one brief per instrument, so it is easy to filter and forward.
  Later, a guarantor with several instruments could get one email with a section for each.
- **Daily brief.**
  The first version sends only the Monday brief.
  Later, a short brief every weekday morning, for that day only, could catch changes made after Monday.
- **LLM summary.**
  The first version uses only rule-based heads-ups, because experiment plans are usually 1 to 5 words.
  An LLM paragraph could come back if plans get richer, or for the last week recap.
  It needs a yes from CF nano before reservation data is sent to an LLM provider.
- **Last week recap.**
  A summary of what was actually done on the instrument last week.
  It would use the instrument's user fields (sample, settings, detectors) and the experiment results.
  Users fill these in during or after the session, so they are empty before the week starts and do not belong in the Monday brief.
