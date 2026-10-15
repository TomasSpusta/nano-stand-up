# Nano Stand-up

Weekly briefs for CF nano instrument guarantors about upcoming use of their instruments.

## Language

**Instrument**:
A piece of CF nano equipment that users reserve, such as a microscope or a lab.
_Avoid_: Equipment, machine, device, tool

**Guarantor**:
The staff member recorded as the owner of an instrument in the booking system.
_Avoid_: Admin, manager, owner, garant

**Brief**:
The weekly message one guarantor gets about one instrument, listing the reservations from Monday to Sunday of that week.
A reservation that runs in from the previous week shows on Monday, and it counts only its hours inside the week.
It shows the reservations as they were when it was built on Monday morning.
It is sent even when the week has no reservations.
_Avoid_: Stand-up, report, newsletter

**Service admin**:
The person who runs Nano Stand-up and gets its error reports.
_Avoid_: Admin, operator, maintainer

**Subscription**:
A guarantor's confirmed request to get the brief for one instrument.
It is active only after the guarantor clicks the confirm link sent to their booking-system email.
It ends when that person is no longer the instrument's guarantor.
_Avoid_: Sign-up, registration, newsletter

**Reservation list**:
The part of a brief that shows each reservation as booked, with no AI rewording.
_Avoid_: Schedule, agenda

**Heads-up**:
One rule's finding about the week that the guarantor may want to act on: the rule's title and the reservations it matched, each by day, time, and user.
Each heads-up comes from a fixed rule, not from an LLM.
_Avoid_: Alert, warning, AI summary, insight

**Heads-up section**:
The part at the top of a brief that holds the week's heads-ups.
The brief leaves it out when no rule matches.
_Avoid_: Heads-up box

**Working hours**:
The normal working day, Monday to Friday, from 07:00 to 19:00 unless the settings `WORKING_DAY_START` and `WORKING_DAY_END` say otherwise.
A reservation is inside them only when it starts and ends on the same weekday, within these times.
Times compare to the minute, as the brief shows them.
_Avoid_: Office hours, business hours, opening hours

**After-hours safety check**:
The extra safety check a guarantor may need to arrange for a reservation outside working hours.
A cancelled reservation needs none, so it is never listed under "Outside working hours".
_Avoid_: Night check, overtime check

**Reservation**:
A booked time slot on an instrument for a user and a project.
Its status is reserved (the normal case, "Rezervováno" in the booking system), requested, or cancelled.
_Avoid_: Booking, appointment, session

**Certificate**:
A user's permission to use one instrument on their own, valid from one date to another.
A user can hold several certificates for the same instrument, for example one from each guarantor who trained them.
_Avoid_: Certification, training, licence

**New user**:
A user whose reservation is one of their first 3 on the instrument since the start of their earliest certificate for it.
The setting `NEW_USER_RESERVATIONS` can change the 3.
A user with no certificate for the instrument is not a new user.
_Avoid_: First-timer, newcomer, beginner

**Note**:
The user's free text on a reservation itself, apart from the experiment plan.
Users often copy the experiment plan into it, so a brief shows it only when it adds something.
_Avoid_: Description, comment

**Private note**:
A note on a reservation that only staff can see.
_Avoid_: Private description, internal note

**Requested reservation**:
A reservation waiting for the guarantor to approve it, so it may not happen.
The booking system calls it "Požadováno" (Requested), statuscode 1.
A brief highlights it and counts it as "waiting for your approval", because the guarantor has to act on it.
_Avoid_: Tentative, pending, unconfirmed

**Cancelled reservation**:
A reservation the user cancelled. The booking system keeps it, and a brief shows it crossed out.
_Avoid_: Deleted, removed

**Experiment plan**:
The user's written description of what they will do in a reservation.
It is stored apart from the reservation and may be missing.
_Avoid_: Plan, description, notes
