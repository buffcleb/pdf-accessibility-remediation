# Screenshots

Images used by the README and the user guide. They show invented documents and made-up names only (Northfield
University, northfield.example), processed by the engine with Local AI and shown in the portal with a test database.

> **Changed 22 October 2026:** eight pictures were retaken for the app as it is now (lists with one search-and-filter
> bar and a pager; the document page's Downloads card and Previous/Next; Needs attention's links and Acknowledge;
> the Groups list; Settings' tabs and Signed-in devices): `my-documents`, `document-page`, `my-settings`,
> `group-admin`, `reports-quality`, `admin-attention`, `admin-groups` and `admin-settings`. They come from the
> accessibility checks' test app (`tests/a11y/serve.py`), whose documents are the same invented ones with simulated
> results, so their counts differ from the older pictures'. The others (`signing-in`, `document-checks`,
> `help-form`, `report-pdf` and the tag manager's) are unchanged.

| File | Shows | Used in |
|---|---|---|
| `signing-in.png` | The sign-in page | people/signing-in |
| `my-documents.png` | My documents: pages left, the upload form with the AI line, the documents list | README, people/submitting-documents |
| `document-page.png` | A document's page: the verdict, the three tiles, the Needs review tab, the downloads | README, people/your-results |
| `document-checks.png` | The Accessibility check tab | people/your-results |
| `report-pdf.png` | The first page of the accessible PDF report | people/your-results |
| `help-form.png` | The Ask for help form | people/getting-help |
| `my-settings.png` | A person's Settings: email notifications and email reports | people/email-and-settings |
| `group-admin.png` | A group admin's view of their group | group-admins/the-admin-area |
| `reports-quality.png` | A live report (Quality) with its chart and table | group-admins/reports |
| `admin-attention.png` | Administration → Attention, with the access check | super-admins/README |
| `admin-groups.png` | Administration → Groups | super-admins/groups-and-people |
| `admin-settings.png` | Administration → Settings, Branding | super-admins/settings |
| `tag-manager/*.png` | The tag manager: the overview, the tag tree, reading order, the tag menu and its windows, read aloud, areas, figure split, lists, footnotes, saving | README (overview), people/tag-manager/ |

Take them at 1440 px wide in the light theme, with sample documents and made-up names only, and give each a
descriptive alt text where it's used.

## Diagrams

> **New 22 October 2026:** the documentation's diagrams, Mermaid charts included, redrawn the same way.

`diagrams/` holds the documentation's diagrams and charts, each as a light and a dark SVG (`<name>.svg`,
`<name>-dark.svg`) in the portal's colours, all drawn the same way. The pages show them with `<picture>`, so the one
matching the reader's theme is shown. They're drawn by [`diagrams/build.py`](diagrams/build.py): change a diagram
there and run `python3 docs/images/diagrams/build.py` to write them all again. Keep each one's alt text in the page
the same as its description in `build.py`. Draw new ones there too, not in Mermaid, so they stay consistent. The
security reviews' diagrams are kept apart, in `evaluation/reviews/images/`, out of the public snapshot.

| File | Shows | Used in |
|---|---|---|
| `service-overview` | What the service runs: the portal, the engine, their data, and what leaves the cluster | first-cluster/README |
| `install-order` | The nine install steps by team, and what each waits for | first-cluster/README |
| `network-paths` | The ways in and out: service and API addresses, SSH, ports between nodes, outbound | first-cluster/network |
| `storage-kinds` | A block disk attached to one server at a time, and the NFS folder every server mounts | first-cluster/storage |
| `patching-cycle` | One server's turn at patching: drain, patch and reboot, uncordon | first-cluster/os |
| `engine-nodes` | The engine's pods on tainted nodes of their own, apart from the server nodes | first-cluster/scaling |
| `pac-adobe-findings` | PAC failures on the Adobe route, by document (a chart) | evaluation/test-reports/2026-10-22-pac-regression-set |
| `how-it-works` | How it works, in brief | the repository README |
| `system-context` | Every part of the service and how they connect | architecture/README |
| `engine-pipeline` | The engine's steps, with its three decisions | architecture/engine-pipeline |
| `tag-editor-save` | Saving edits in the tag manager (sequence) | architecture/tag-editor |
| `service-access` | Who can use a service, and where pages come from | architecture/services-and-quotas, group-admins/services-and-pages |
| `document-path` | One document's path through the portal and engine (sequence) | architecture/services-and-quotas |
| `adobe-route` | The Adobe route (sequence) | architecture/adobe-route |
| `verification-layers` | The three layers of checks, then a person | architecture/verification |
| `sign-in` | Signing in with an emailed code (sequence) | architecture/security-and-privacy |
| `portal-architecture` | The portal as built | design/portal-spec |
| `document-lifecycle` | A document's life, from upload to its files being removed | people/README |
| `scripting-flow` | A script's round trip with an API token (sequence) | people/scripting |
| `rollback-decision` | Which way to roll back | operations/rollback |
| `secret-flow` | Where secrets are kept, and how deploy.sh splits them | operations/secret-rotation |
| `reference-rke2` | The reference deployment | deployment/reference-rke2 |
| `aws-plan` | The AWS plan | deployment/aws |
