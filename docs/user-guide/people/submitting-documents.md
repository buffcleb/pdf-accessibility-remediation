# Submitting documents

[Guide for people submitting documents](README.md) › Submitting documents

## Before you upload

- **PDFs only.** Convert Word, PowerPoint and other files to PDF first, using the application's own "Save as
  PDF" or "Export to PDF". If it has an accessibility option ("tagged PDF", "document structure tags"), turn it
  on: the result starts closer to accessible.
- **Opens without a password.** A PDF that asks for a password to open can't be read; remove the password first. A PDF
  that opens freely but restricts printing or editing is fine: the accessible copy keeps the same restrictions, with
  access for screen readers turned on (PDF/UA requires it).
- **Signed PDFs wait for you.** Making a file accessible rewrites it, so a signed or certified PDF's accessible copy
  won't carry a valid signature. It waits on the upload page and its own page until you choose **Send** (sign the
  copy again afterwards if you need a signature) or **Cancel**. Nothing is charged until you send it. A check only
  changes nothing, so it isn't held; its report says the PDF is signed.
- **XFA forms.** Forms made with Adobe LiveCycle Designer (XFA) come in two kinds. A *dynamic* one draws its pages
  as it opens and has no standard form underneath, so it can't be made accessible and the upload refuses it: open it
  in Acrobat, save it as a standard PDF form (not XFA), and upload that. A *static* one also holds a standard form,
  which is kept and made accessible; the XFA part is removed (the report says so).
- **Size limits.** Your administrators set the largest file, the most pages per document and the most files per
  upload (100 MB, 2,000 pages and 20 files by default). The upload says so if a file is over.
- **Scans work,** but the clearer the scan the better the result: scan at 300 dpi where you can.

## Your pages

At the top of **My documents**, a strip shows the pages you can still use, one cell per processing option and AI
level, for example "Standard: **94** pages left". The cell fills to show the share left, and says **Low** or
**None left** when it's nearly or completely used up, with how to get more. **Details** opens the breakdown:
used, in progress, your limit and left.

Each page of each PDF uses one page of your allowance **for each option it uses**. A 10-page document processed
with Standard and Local AI uses 10 Standard pages and 10 Local AI pages. Some options are unlimited; they say
"No limit".

## Uploading

1. On **My documents**, drag PDFs onto the **Submit PDFs** box, or choose **Choose PDFs**. They're listed with
   their sizes; **Remove** takes one off the list.
2. Below the list, one line per choice shows what will be used:
   - **Processing** (only when you've been given more than one option): *Standard* is this service's own engine
     on its own servers. *Adobe Autotag*, if offered, sends documents to Adobe PDF Services (see below).
   - **AI**: the AI level and, when the level offers more than one kind, the kind of document. For example
     *Local AI* or *Premium AI · Math and science*. Under it, a short description of the level.
3. Choose **Remediate**, or **Check only** (below).

![My documents for a person in Northfield University: Standard and Commercial AI pages left at the top; the Submit PDFs box with Choose PDFs, the AI line (Commercial AI, 292 pages left), More options, and the Remediate and Check only buttons; then Your documents, a list of five documents with their pages, status (Done, or Processing) and result (Passes PDF/UA-1).](../../images/my-documents.png)

The documents appear under **Your documents** straight away, as *Waiting*, then *In progress* with the current
step. You get an email when each is done, so you don't need to keep the page open.

## Choosing the AI

AI writes image descriptions (alt text), reads tables, works out tricky reading orders, reads specialist
notation (math, chemistry, dosages) and corrects text read from scans. Which **AI level** you can use is set by
your administrators; the default is preselected.

- **Change** next to the AI line picks another level or kind for the whole upload.
- **Kind of document:** some levels offer kinds, such as *Math and science* or *Forms*, so the AI best suited to
  the document is used. *General* suits everything else. You never need to know which AI model that is. If a
  kind isn't available when the document is processed, General is used and the document's page says so.
- **Let AI decide** (if offered) picks the kind for you. Before processing, the first pages' text (never the
  images) is read and the best-fitting kind is used; when it isn't sure, or a scan has no text yet, General is
  used. The document's page says what was picked and how sure it was, and whether the text was read by an
  outside service.
- **One file different from the rest:** with several files, **Change AI** next to a file gives it its own level
  and kind. **Use the same as the others** puts it back.
- **Set by your group:** if your group uses one AI level for everything, the AI line says so and there's nothing
  to choose.
- **External:** your group may mark options that send documents to an outside provider with **External**. If
  your administrators have turned on a notice for an option, it appears when you pick it, saying where documents
  go.

## Check only

**Check only** checks the documents and reports problems without changing them: the accessibility check and the
review list, but no remediated file. Use it to see how far a document is from accessible, or to check a file
someone else made accessible.

The line under the buttons says what a check uses. Usually it's free; an AI level can also be set to count its
pages for checks, or to check without AI.

## More options

Under **More options**, **Ignore the existing tags** is for a PDF that is already tagged, but badly (headings tagged
as paragraphs, a jumbled reading order, tables as plain text). Normally a document's own tags are kept and repaired,
since they're usually its author's; with this ticked they're set aside and the document is tagged afresh from what's
on the page. The report says the tags were ignored at your request, and the document's page shows "existing tags
ignored". It changes nothing for **Check only**, and it costs the same.

**Review every figure with AI** has the AI checker look at every figure and formula,
instead of the ones flagged as doubtful plus a sample. It's slower and uses more AI; use it for documents where
accuracy matters most. Some AI levels don't offer it.

## Adobe Autotag

If your administrators offer it, *Adobe Autotag* sends documents to Adobe PDF Services, which adds the tags.

- You get **Adobe's file exactly as it comes back** (labeled **Adobe tagged**), checked by this service like any
  other result, plus Adobe's own report (an Excel file) among the report files.
- Adobe doesn't describe images and doesn't guarantee its tags pass. So **Write alt text for images with AI** is
  ticked by default: you also get an **improved copy**, looking exactly the same, with AI descriptions of its
  images, read-aloud text for math and chemistry the PDF's own text can't voice, any font fixes that don't change how
  pages look, and bookmarks made from its headings. It's named for what it contains, for example *Adobe + AI alt
  text*. It uses pages of the AI level you pick, given back if the AI wasn't needed.
- Check only never goes to Adobe: checks are always made by this service.

## Confirming before sending

Some options ask you to confirm before anything is sent, typically ones that send documents outside. The upload
then lists each document with its page count and **Send** and **Cancel** buttons; nothing is sent and no pages
are used until you choose Send. You can also confirm from the document's page.

## Waiting

Documents wait their turn in a queue. You may see:

- **Waiting:** others are ahead, or you already have several documents in progress (each person has a limit on
  how many run at once).
- **Large document:** documents over a certain size (200 pages by default) take turns with smaller ones so they
  don't hold everyone else up. It may take longer to start.
- **Waiting for a free place:** the option you chose (an AI level, for example) handles a set number of documents
  at once. Yours starts when one of them finishes.
- **Waiting for tomorrow:** an option has a daily page limit and it's been reached. The document waits until the
  next day, or until an administrator raises the limit; it doesn't fail.
- **Paused by support:** support has paused that processing option for now. Documents wait and go ahead when it's
  resumed.

## When an upload is refused

The message says why, naming the option and where the limit is:

- **Not enough pages:** your group's pool or your own limit has run out. A personal limit can't give you pages
  your group's pool doesn't have. The message links to how to get more (usually your group admin).
- **Not available to you:** your group keeps documents on its own servers (local-only), or you haven't been
  given that option. Choose another, or ask your group admin.
- **Too large, too many pages, password-protected, not a PDF, a dynamic XFA form:** fix the file and upload it again.
- **Too many uploads in an hour:** each person has an hourly limit on uploads (and on downloads and report
  exports), set by the people who run the service. Wait a while and try again.

See [Troubleshooting](troubleshooting.md) for more.
