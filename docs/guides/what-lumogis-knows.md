# What Lumogis knows

The **What Lumogis knows** page lists the entries Lumogis has learned from your documents, chats and notes: people, organisations, projects, concepts and anything else it found. Open it from **Search → Browse what Lumogis knows**.

It works the same whether the knowledge graph is switched on or off.

## What the page shows

- **Tabs by kind** — All, People, Organisations, Projects, Concepts and Other, each with a count. "Other" collects everything that is not one of the four named kinds (for example files and code-related entries).
- **A filter** — all entries, only those shared with your household, or only yours.
- **A sort** — most mentioned, recently mentioned, or A–Z.
- **For each entry** — its name, up to three other names ("also: …"), how many items mention it, and when it was last mentioned.

Entries Lumogis is not sure about yet are not listed. A new household has an empty page: *"Lumogis hasn't learned anything yet."* Add documents or chat and entries will appear.

## Who sees what

You see your own entries and the entries other members have **shared with the household**. You never see another member's personal entries.

For an entry another member shared, the list does not show how many items mention it or when, because those numbers describe their private items.

## Mentioned in

Open an entry to see where it came from. The **Mentioned in** section lists documents, chats and additions made by an AI tool that mention the entry, newest first.

- It lists only items **you** can open. On another member's shared entry you see only what they have shared with the household, never their personal documents or chats.
- Documents link to the document. Chats and AI-tool additions are shown as a dated label; Lumogis Web cannot yet open a past chat from here.
- The count on the list is the number of items that mention the entry **and still exist**. Delete a chat and the count drops.

## Renaming an entry and editing its other names

If you own an entry (it is yours, not a copy a household member shared with you), the entry page has an **Edit names** button.

- **Name** — the name Lumogis shows.
- **Other names** — other ways the entry is written ("Meier" for "Dr. Meier"). Add or remove them with the list in the dialog.

Names are plain text: they can't be empty, can't contain control characters, and an entry can have at most 50 other names.

If you change the name, the **old name is kept as another name**, marked "kept" in the dialog. Remove it if you don't want it kept. Lumogis warns you: *"If you remove a name, Lumogis may learn it again as a new entry the next time it reads it."*

### What this does not guarantee

- **The old name helps Lumogis recognise the entry only when the surrounding context matches.** When Lumogis reads a new mention, it joins it to an existing entry only when the name or one of its other names matches **and** at least two of the context words it recorded overlap. Without that overlap you can get a new entry with the old name, which you can then tidy up (merging entries is planned).
- **Search finds an entry by its current name only**, not by its other names. After a rename, searching for the old name does not find the entry.
- **A name another entry already uses is refused.** If another of your entries is called that, or lists it as another name, Lumogis tells you and links to that entry. Lumogis may also tell you that it has an entry of that name it isn't sure about yet.
- If you open the dialog and an entry changes before you save (for example Lumogis learns a new other name), your other names are not overwritten: only the changes you made are applied. If the entry's version moved on, you are asked to reload and try again.

## Correcting what kind an entry is

Lumogis sometimes files an entry under the wrong kind: "Meier Pharmacy" as a person, say. If you own the entry, open it, choose **This is wrong…**, then **It's not a person, it's a…**, pick the right kind (person, organisation, project or concept) and press **Change**.

The entry keeps its name, its other names, the documents and chats that mention it and its links. Search and the household's shared copy show the new kind at once. The **map** follows at its next daily tidy-up, the same as after a rename, so it can show the old kind for up to a day.

### What Lumogis remembers

When you change a kind, Lumogis also remembers the correction, **for you only**: from then on, when it reads that name *as the kind it was wrong about*, it stores it as the kind you picked. So "Meier Pharmacy" read as a person again becomes an organisation again, without you correcting it twice. The dialog tells you this before you press **Change**.

- It is kept **for that name and the kind it was read as**. Other names of the entry are not covered: if Lumogis reads "Meier Apotheke" as a person, that is a new reading.
- It applies to what Lumogis reads on its own (documents, chats, "extract entities"). An entry you add yourself through an AI tool with an explicit kind keeps the kind you gave.
- It is kept after you rename or remove the entry, and it is included in your data export. To undo it, **change the entry back**. A list where you can see and remove what Lumogis remembers is coming with "names Lumogis won't learn".

### When a kind can't be changed

If you already have an entry **of the kind you picked** with the same name or one of the same other names, Lumogis refuses with *"You already have an organisation called …"* and links to that entry. The message offers **Merge with …**: the entry that already has the kind you picked stays, this one is merged into it (see [Merging two entries](#merging-two-entries-that-are-the-same)), and you land on the entry that stays.

- **Two entries with the same name.** If you correct one, the other cannot be changed to the same kind while both exist. Merge them, or keep them apart. Lumogis stops making new wrong entries, but the old one stays until you merge it.
- **A re-read it can't tie to your entry.** When Lumogis reads the name in a context that shares nothing with what it recorded for your entry, it can still add a second entry with that name, of the corrected kind. This is the same duplicate that exists today for any name; merging tidies it.
- Existing entries of the old kind with the same name are **not** changed automatically.

## Merging two entries that are the same

Sometimes Lumogis keeps two entries for one thing: "Dr. Meier" and "Dr. A. Meier". Open the entry you want to keep, choose **This is wrong…**, then **It's the same as…**, search for the other entry and press **Merge**. Only your own entries can be merged, and an entry Lumogis is still checking can't be merged yet.

- **The entry whose page you are on stays.** It keeps its name by default and the other name becomes one of its other names, so Lumogis still recognises it. Choose **Keep "…"** to give the entry the other entry's name instead (the old name then becomes another name).
- **Everything moves over:** documents, chats, links between entries and other names. If both entries mention the same document, it is listed once.
- **If the kinds differ**, the entry that stays keeps its kind, and Lumogis remembers that the other name belongs to that kind (the same memory as a kind correction). The dialog says so before you merge.
- **If exactly one of the two is shared with your household**, the dialog says what becomes visible: either the other name appears for the household as another name for the shared entry, or the entry is now shared. Merges Lumogis makes by itself never change what your household sees: if one of two duplicates is shared, Lumogis leaves them for you to merge here.

### History and undo

Every merge is written down, so you can reverse it. An entry that has been merged into shows a **History** section: who merged it (you, Lumogis automatically, the review queue, or an admin), when, and **Undo merge**. Merges made automatically or by an admin for you appear there too, and you can undo them.

- **Undo brings the other entry back as its own entry** with its documents, links, other names and, if it was shared before the merge, its shared copy. The entry that stayed keeps everything it gained since, such as new mentions or other names you added.
- **Lumogis won't merge the two again.** An undone pair is remembered as "not the same", so automatic tidying leaves them alone.
- **Undo goes newest first.** If you merged several entries into one, only the newest merge can be undone; History says which newer merge to undo first.
- **Mentions added since.** After an undo, History lists the mentions the entry that stayed has gained since the merge. Tick the ones that belong to the restored entry and choose **Move to …**.
- **Some things don't come back.** The other entry's summary is not kept on the entry that stays, so it returns with the restored entry only. If a document was deleted since the merge, its mention is not put back ("Some items were deleted since the merge and could not be put back."). If you removed and re-added one of the other names after the merge, undo removes it. The restored entry gets its old name even if an entry with that name has appeared since.
- **Older merges can't be undone.** Merges made before this feature existed have no record.

The map follows an undo at its next daily tidy-up, like a rename; the entry page, search and chat are right at once. The household's shared map can show stale links for up to a day.

### The map

The rename is saved at once. Search, the entry page and the household's shared copy update within seconds. The **map** (the knowledge graph) follows at its next daily tidy-up, so it can show the old name for up to a day.

## Deleting an entry so Lumogis does not learn it again

If Lumogis made an entry that is just wrong, open it, choose **This is wrong…**, then **Delete…**. Only the owner of an entry can delete it. Delete removes the entry, its mentions, its links to other entries, its merge records and its search entries, and, if you had shared it, the household's copy. **Your documents, chats and notes are not changed.**

The entry's name and its other names go onto your list of **names Lumogis won't learn**, at the kind the entry had, so reading the same document again cannot bring it back. The kind matters: deleting "Total" the person does not stop Lumogis learning "Total" the organisation. If the entry was really another of your entries under a different kind, merge it into that entry instead of deleting it. A name that another of your entries of the same kind still uses stays learnable for that entry, and the page that follows a delete says so.

- **Your list.** **Names Lumogis won't learn** in your account settings (`/me/wont-learn`) shows the names, your kind corrections and the shared entries you have hidden. **Remove** on a name lets Lumogis learn it again.
- **Re-reading.** Removing a name offers to re-read the documents that mentioned the entry. Only documents can be re-read (up to 50 at a time, the most recent first; the rest are listed from Documents). Chats and notes are not re-read.
- **Kind corrections.** If a delete replaced a kind correction you had made, removing the name brings the correction back. Giving a live entry that name again (renaming it, adding it as another name, or changing its kind to the blocked one) also lifts the block.
- **The household.** Deleting a shared entry removes it for everyone, and the dialog says so.
- **There is no trash.** A delete cannot be undone, and merges into the entry can no longer be undone either. Removing a name only lets Lumogis learn it again; it does not bring the old entry back.

### What a delete does not reach

- **Summaries, chat memories, briefings and the names stored with past chats** may still contain the words. Lumogis has no "forget completely" yet.
- **Your list and your data export** keep the deleted names, and the paths of the documents that mentioned them, until you remove them from your list.
- **Explicit additions win.** An entry or link created on purpose through an AI tool (`add_entity`, `add_relation`) is still created. Later extracted mentions of that name at that kind are dropped until you remove the name from your list. Entries an AI tool picks by itself while saving a memory are checked against your list.
- **A read that started before the delete** can bring the entry back once. Deleting it again holds.
- **If Lumogis cannot read your list** while it reads a document, it does not guess: it keeps no entries from that document yet and reads the file again at the next rescan (the start-up scan, an admin **Rescan**, or the next inbox poll when the inbox is in poll mode). Chat summaries that fail the same way are retried and, after enough failures, skipped. Email attachments and external documents are read again only when they next change.
- **A hidden name can still appear as a neighbour.** With the map on, a name you hid is never the starting point of a chat answer, but it can still appear in another entry's "relates to" line.
- **A merge lifts a block only through the merged entry's own name.** Names the survivor gains from the loser's other names are not lifted; remove them from your list if needed.
- **Hide goes with a merge.** If the owner merges a shared entry you hid into another, the survivor shows for you again. Hide it again.
- **Deleting over a kind correction.** If you delete an entry whose name already carried a kind correction from a different kind's delete, that correction becomes a plain block; removing the other block no longer brings it back.
- **Decomposed accents.** Names are compared in a normalised form; two spellings that differ only in how an accent is stored may be treated as different.
- **The map** removes the entry's node within seconds and retries for a while if the graph service is down; the daily tidy-up is the backstop.

## Hiding someone else's entry

An entry another household member shared is not yours to delete, so the entry page shows **Hide from my list** instead of the **This is wrong…** menu. Hide removes it from **your** lists, counts, search, Lumogis's answers in chat and your briefings, and nobody else's. It is a view preference, not access control: it can be undone from the entry page or from **Hidden from your list** under **Names Lumogis won't learn** with **Show again**. If the owner deletes the entry, your Hide goes with it.

## For operators

The page uses these routes under `/api/v1/kg`: `GET /entities`, `GET /entities/{id}/mentions` and `PATCH /entities/{id}`, plus, for merging, `POST /entities/{id}/merge`, `GET /entities/{id}/history`, `POST /merges/{id}/undo` and `POST /entities/{id}/mentions/move`. For delete and Hide: `DELETE /entities/{id}?version=N`, `POST` and `DELETE /entities/{id}/hide`, `GET /name-rules` and `POST /name-rules/remove`. The won't-learn list lives in `entity_name_rules` (action `block`; migration 084 also adds `entity_hidden` and `entity_deletions`, retry records of ids only). A delete clears the search points and the graph node in the background and retries every 5 minutes. Undo records live in the `entity_merges` table (migration 083). They are not part of the per-user export or the admin JSON backup; `make backup` includes them. They read Postgres only, identical with `GRAPH_MODE` set to `disabled`, `inprocess` or `service`. See the [reference manual](../LUMOGIS_REFERENCE_MANUAL.md) §8.
