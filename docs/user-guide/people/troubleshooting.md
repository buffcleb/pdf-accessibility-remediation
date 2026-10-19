# Troubleshooting

[Guide for people submitting documents](README.md) › Troubleshooting

## Signing in

| What you see | What it means | What to do |
|---|---|---|
| No code arrives | It may be in junk, or delayed | Wait a minute, check junk, then **Send a new code**. If codes never arrive, contact your administrators: they can issue one another way |
| "Your email address isn't set up for this service" | Neither your address nor your email's domain is linked to a group | Contact the people who run the service for your organization; they can add your domain, or just your address |
| "That code is wrong or has expired" | Codes last 10 minutes, and only the newest works | Request a new code and use that one |
| "Too many attempts" | Too many codes asked for, or tried, in a short time | Wait a few minutes and try again |
| An email saying your account can't sign in, instead of a code | An administrator disabled your account (the sign-in page doesn't say, so it can't tell anyone else) | Contact your group's administrator |
| "Waiting for approval" on My documents | Your group approves new people first | Wait for the email; **Check again** refreshes |

## Uploading

| What you see | What it means | What to do |
|---|---|---|
| "Your group has 12 Standard pages left and this document needs 40" (or "You have…") | The option named has run out: your own limit, your group's pool, or the whole service's | The message links to how to get more; usually ask your group admin |
| The option is refused as not available to you | Your group keeps documents on its own servers (local-only), or you haven't been given that option | Choose another option, or ask your group admin |
| "Files can be at most … MB" / "Documents can have at most … pages" | Over the limits your administrators set | Split the document, or ask an administrator |
| "This PDF is password-protected" | The PDF is encrypted | Save a copy without the password, and upload that |
| "This is a dynamic XFA form…" | A LiveCycle form that draws its pages as it opens, with no standard form to tag | Open it in Acrobat, save it as a standard PDF form (not XFA), and upload that |
| "This file isn't a PDF" / "This PDF is damaged and can't be read" | The file isn't a readable PDF | Export it to PDF again from the original application |
| "Letting AI decide the kind isn't available here" | That choice was switched off since the page loaded | Reload the page and choose a kind |
| "This PDF has no pages" / "This PDF couldn't be checked…" | The file is empty, damaged, or too large or complex to open safely | Export it to PDF again, or split it |
| "That's a lot of uploads in an hour" (or downloads, or exports) | Each person has an hourly limit on these | Wait a while and try again, or ask your administrator |
| A signed PDF waits with **Send** and **Cancel** | Making it accessible removes the signature's validity | Choose **Send** (and sign the copy again), or **Cancel** |

## Waiting and processing

| What you see | What it means | What to do |
|---|---|---|
| **Waiting** for a long time | Others are ahead, or you already have several documents running | Nothing; it goes ahead in turn |
| **Large document** | Big documents take turns with smaller ones | Nothing; it may take longer to start |
| **Waiting for a free place** | The option you chose is handling as many documents as it can at once | Nothing; it starts when one finishes |
| **Waiting for tomorrow** | An option's daily limit has been reached | It goes ahead the next day, or ask an administrator |
| **Paused by support** | Support paused that option for now | It goes ahead when it's resumed |
| **Failed** | Something went wrong; the page says what | Try again, or ask for help. If the AI was unavailable, your pages are given back |
| "Processed with General" | The kind you chose wasn't available, so the level's general AI was used | Usually fine; ask for help if the results are poor |

## Results

| What you see | What it means | What to do |
|---|---|---|
| "Doesn't fully pass yet" (or **Needs work** in the list) | Some rules still fail | See **Still failing** in the report. Font problems are usually solved in the modified file, if there is one |
| Two files | The document can't pass without a visible change | See [Which file to use](your-results.md#which-file-to-use) |
| A long review list | Many things a machine couldn't be sure about | Start with **High**; suggestions are optional |
| Alt text that's wrong | AI-written descriptions are drafts | Fix it in the [tag manager](tag-manager/figures.md), or ask for help |
| The files are gone | Files are kept for a set time (shown on the document), then removed; or the document was [marked done](your-results.md#when-youre-done-with-a-document) | Upload the document again if you still need it |
| I marked a document done by mistake | The files are kept for a short hold first | On its page, choose **Keep the files** before the time shown |

Still stuck? [Ask for help](getting-help.md) on the document's page.
