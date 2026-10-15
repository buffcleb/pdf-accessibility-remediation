# First-time setup

[Guide for super admins](README.md) › First-time setup

From a newly installed service to the first group using it. Each step links to the details.

## Before you sign in

The person installing the service ([Deployment](../../deployment/README.md)) sets your email address in
`PORTAL_BOOTSTRAP_SUPER_ADMINS` and configures email. Sign in with that address; you're a super admin from the
first sign-in.

## Checklist

1. **Brand it.** **Settings → Branding:** the service and organization names, the email sender name, an accent
   color and a logo. ([Settings](settings.md#branding))
2. **Say where to get help.** **Settings → Help and messages:** the help email address, the link for requesting
   pages, and the messages people see when they're not in a group, disabled or out of pages.
3. **Test email.** **Settings → Email → Send test email.** Sign-in codes depend on it. Check the test message
   arrives, and that it isn't marked as spam. ([Settings](settings.md#email))
4. **Check the processing options.** **Services and AI levels:** *Standard* is switched on. Decide whether to
   switch on *Adobe Autotag* (it needs Adobe credentials on the engine first).
   ([Services and AI levels](services-and-ai-levels.md))
5. **Publish at least one AI level.** The seeded *Local AI* level points at the gateway's local model; switch it on,
   or **Add an AI level** for another gateway model. Choose its review model, and whether it reads specialist
   notation and corrects OCR. Optionally add **kinds of document**.
6. **Set upload limits.** **Settings → Uploads and limits:** largest file, pages per document, files per upload,
   each person's queue, large documents, and whether to offer **Let AI decide**.
7. **Set retention.** **Settings → Sign-in and data:** how long files and records are kept, sign-in timeouts, and
   whether new groups approve members first.
8. **Create a group.** **Groups → Create group:** its name and email domains, so people on those domains join it.
   For individual people (a gmail.com address, a guest), add their addresses under **People allowed by address**
   on the group's settings instead of a domain.
   Make someone its group admin. ([Groups and people](groups-and-people.md))
9. **Offer it services.** **+ Offer a service to a group:** the service, who gets it, pages for the pool and the
   default limit per person. Do it once for the processing option and once for each AI level. The set-up checks
   one of the people can actually use it.
10. **Try it.** Sign in as someone in the group (or check with **Attention → Why can't this person use a service?**)
    and process a short document.

## After that

- Watch **Attention** for people waiting for approval, pools running low and failing documents.
- Handle **Help requests** as they come in.
- Look at **Reports → Quality** and **Remaining failures** now and then to see how documents are doing.
- Choose your own **email reports** (Settings, top right): the **Service summary** gives a monthly overview.
