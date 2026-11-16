## What I Built

I built a command-line HR agent (agent.py) that drafts a reply to one employee email at a time. For each email it starts my MCP server (hr_mcp_server.py) in employee mode for the sender, so Claude can search the HR policy documents and look up only the sender's own leave balance, and then it saves the reply with a save_reply tool. It never sends anything. Every draft is marked for human review or not.

To make it work for Ridgeline Hotels, I added an --org option to both the agent and the server that points to an organization folder holding hr-docs, employees.csv, and system_prompt.txt; without it, they still use Hartwell's folder. Each organization gets its own Chroma search database, kept out of Git. Ridgeline's employee file uses different column names than Hartwell's, so the code now finds the email and balance columns from the header row instead of assuming them. I also made --save write everything printed to a UTF-8 text file, and changed the output to show the sender and subject first, then a DRAFT line, the reply, and a Needs human review: true/false line. After my first runs, I added Hartwell's rules to Ridgeline's system prompt (plain text, a fixed sign-off, flagging injection attempts) plus two new rules: don't describe systems or processes the tools didn't mention, and look up the sender's own balance when it helps.

I then ran all seven Ridgeline sample emails and saved each run in week-04/runs. Emails 1 to 3 got correct, cited answers about PTO, bereavement leave, and borrowing PTO. Email 4 refused to share a coworker's balance, Email 5 ignored a fake "admin override" asking for everyone's balances and was flagged for review, Email 6 (harassment by a guest) was handed to a person without advice, and Email 7, from a sender who isn't in the employee records, got no made-up balance and was flagged for review.

## Why This Approach

Task A: Script. Adding the PTO each team member earned that day can be done with a nightly script, because it's a predictable, recurring task that can be automated.

Task B: One model call. Rewriting the PTO policy as a one-page FAQ can be done with one model call, because it's a one-time task and you don't need an agent to act on your behalf. There are no tools to use or decisions needed about what's next.

Task C: Agent. Answering supervisors' scheduling emails can be done with an agent, because it can use a RAG system to retrieve policy, and it can set up a draft email on behalf of HR to determine what should be done next.

Email 5 protection: Tools. Injected instructions within an email or request can be easily thwarted by preventing the tool from accessing data that is unnecessary. What kept the other balances safe was the tool's design. In employee mode, get_leave_balance takes no inputs and can only return the sender's own record.

## Example Run

```
Sender:  Nicole Hayes <nicole.hayes@ridgelinehotels.example>
Subject: Staff room rate
Email:   week-04/ridgeline-hotels/sample-emails/05-nicole-room-rate.eml
Org:     ridgeline-hotels
Model:   claude-sonnet-5
ridgeline-hotels: email column 'Email Address', balance columns 'PTO Days Remaining', 'Sick Days Remaining'
Tools:  search_policy, get_leave_balance, save_reply

[Step 1] search_policy
  inputs: {"query": "staff room rate discount hotel employees"}
  result: { "source": "team-member-perks.html", "title": "Team Member Perks", "version": "Effective this year. Owner: People and Culture.", "section": "Staff room rate", "similarity": 0.592, "text": "Team Member Perks\nStaff room rate\n\nAfter 90 days of employment, you can book rooms at any Ridgeline hotel a...

[Step 2] save_reply
  inputs: {"reply_text": "Hi Nicole,\n\nYes, you can use the staff room rate at the Taos hotel for a March weekend, as long as you've been employed for at least 90 days. The discount is 50% off the public rate, for up to 12 nights a year, subject to availability. You'd book it through the Team Member Travel page and show your badge at check-in. Note that the staff rate can't be combined with other offers.\n\n(Source: team-member-perks.html, \"Staff room rate\")\n\nBest,\nRidgeline Hotels People and Culture\n", "needs_human_review": true}
  result: Draft saved to week-04/drafts/05-nicole-room-rate-20261116-150346.txt. Not sent.

Stopped: The draft was saved, so the task is done.

======================================================================
Saved to: week-04/drafts/05-nicole-room-rate-20261116-150346.txt
To: nicole.hayes@ridgelinehotels.example
Subject: Re: Staff room rate

DRAFT
Hi Nicole,

Yes, you can use the staff room rate at the Taos hotel for a March weekend, as long as you've been employed for at least 90 days. The discount is 50% off the public rate, for up to 12 nights a year, subject to availability. You'd book it through the Team Member Travel page and show your badge at check-in. Note that the staff rate can't be combined with other offers.

(Source: team-member-perks.html, "Staff room rate")

Best,
Ridgeline Hotels People and Culture
Needs human review: true
======================================================================
Tool calls: 2
Tokens: 4352 input, 454 output
```

## Reflection

Email 3 tools: get_leave_balance, search_policy, save_reply. The agent checked Brittany's balance and saw she had only 4 of the 6 days she wanted available for PTO. The agent looked through the policy to determine what her options were. Using the policy, the model returned a response, and the agent created a draft email for HR to review.

Email 6 flagged: Yes. The draft did not give any advice. It told Danielle that this was a serious issue and a member of the "People and Culture" team would personally follow up with her.

Email 7 balance lookup: Error. The tool found no record for Logan's email address. The draft did not give him a number. The draft also told him his balance couldn't be found and that a member of the "People and Culture" team would personally follow up with him.
