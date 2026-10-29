# Figures

[The tag manager](README.md) › Figures

## Alt text

Select a figure and change its **Alt text** in the editing panel or in **Edit tag…** ([Editing a
tag](editing-a-tag.md#what-can-be-changed)). AI-written alt text is a draft: read it against the image. Does it say
what the image is *for*? A chart's description should give the trend or the key values, not just "a bar chart".

**Decorative** marks a figure that needs no description (a border, a flourish): screen readers skip it, because the
tag is removed and its content becomes an artifact. **Mark as artifact → Decorative** in the tag menu does the same.

## Splitting a figure

**Split the figure…** (in **Merge and split**) is for a figure or formula made of several pieces, such as a floor
plan with separate parts, a chemistry scheme or a figure with panels. It shows the figure with each piece outlined.

![The Split the figure window for a floor plan of Room 214, with each piece outlined by a dashed box: the room, the
corridor, Stairs A, Stairs B, the red arrows and the eyewash station. "0 of 15 pieces selected", with All and None.
Below: "Make them a" set to Figure, the alt text "Stairs A and Stairs B, the two ways out" for the new figure, and
"Floor plan of Room 214 with the way out to Stairs A and Stairs B" for what stays. Buttons: Apply and
Cancel.](../../../images/tag-manager/figure-split.png)

1. Click the pieces that belong together, such as a drawing and its labels. A click picks the smallest piece under
   the pointer, so a label inside a drawing can be picked. Or drag a box round them to pick everything inside it
   (Shift-drag adds to what's picked).
2. Choose what they become (a figure, caption, formula or paragraph) and write its alt text, then make it.
3. Repeat for each part, and redo the alt text of what stays.
4. **Apply.** The tag tree shows the new figures right after the figure they come from, and the page outlines each
   (dashed until saved).

When a scheme is drawn as a few big pieces that run across its parts (some documents arrive tagged that way), choose
**Cut a box** instead: drag a box over one part, and exactly what's inside it becomes the new figure. Each line goes
with the side of the box that holds its middle, so a long arrow crossing the edge stays whole; the page looks exactly
the same.

Drawing an [area](areas.md) over part of a figure offers the same thing for the pieces inside it (**Make them a figure
of their own…**).

## Describe with AI

If **Describe with AI** is on for you (your group admins decide), it sits under the alt text box when splitting a
figure: it shows the AI the part you picked or boxed and fills in its description, for you to check and correct.
Each use is one AI call at your document's AI level. It's also under the alt text box in [Areas](areas.md), when
untagged items are tagged as a figure or formula: there it shows the AI the area you drew. (**New 27 October
2026**)

## A drawing that isn't tagged as a figure

A diagram made of lines and labels is sometimes read as text and drawings rather than one figure. Draw an
[area](areas.md) over it, then tag the checked items as a **Figure** with its alt text.
