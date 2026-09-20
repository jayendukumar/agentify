# Axyntro Brand Standards

Version 2.0 — lighter digital system based on the approved blue-to-violet Axyntro violet-spark identity.

## 1. Brand character

Axyntro should feel intelligent, transformational, precise and optimistic. The three incoming process paths represent conventional systems; the blue-to-violet A represents agent-based transformation; the violet spark represents the moment intelligence is activated.

## 2. Core colour palette

| Token | Hex | RGB | Primary role |
|---|---|---:|---|
| Royal Navy | `#18285F` | 24, 40, 95 | Headings, navigation and structural elements |
| Wordmark Navy | `#011141` | 1, 17, 65 | Approved Axyntro wordmark only |
| Footer Indigo | `#0A1560` | 10, 21, 96 | Footer and occasional dark surfaces |
| Agent Blue | `#006FFB` | 0, 111, 251 | Primary buttons, links and selected states |
| Button Blue | `#0059D6` | 0, 89, 214 | Hover and pressed states |
| Intelligence Cyan | `#00A4F7` | 0, 164, 247 | Highlights, data visualization, focus accents |
| Transform Violet | `#8A2CFC` | 138, 44, 252 | Spark, campaign accent, transformation moments |
| Spark Violet | `#A438FA` | 164, 56, 250 | Hover accent and controlled gradient highlight |

### Approved brand gradient

`linear-gradient(135deg, #00A4F7 0%, #006FFB 38%, #8A2CFC 100%)`

Use the gradient for the logo, hero accents, selected illustrations and small campaign highlights. Do not use it behind paragraphs, tables or form fields.

### Approved light hero gradient

`linear-gradient(135deg, #F7F9FF 0%, #EDF4FF 55%, #F4ECFF 100%)`

Use this as the default website hero and campaign background. It provides the brand atmosphere without creating a heavy dark first impression.

## 3. Neutral palette

| Token | Hex | Use |
|---|---|---|
| Royal Navy | `#18285F` | Primary headings |
| Slate | `#344054` | Secondary text |
| Muted | `#667085` | Metadata and placeholders |
| Border | `#D0D5DD` | Borders and dividers |
| Mist | `#EAECF0` | Disabled surfaces |
| Page Canvas | `#F7F9FF` | Main website and product background |
| Blue Mist | `#EDF4FF` | Highlighted blue sections |
| Violet Mist | `#F4ECFF` | Transformation-themed sections |
| White | `#FFFFFF` | Main canvas and reversed text |

## 4. Semantic colours

Semantic colours communicate status and must not be replaced by brand violet or blue.

| Status | Text/icon | Background |
|---|---|---|
| Success | `#067647` | `#ECFDF3` |
| Warning | `#B54708` | `#FFFAEB` |
| Error | `#B42318` | `#FEF3F2` |
| Information | `#026AA2` | `#F0F9FF` |

Always pair status colour with an icon and text label; never rely on colour alone.

## 5. Colour proportions

- 70% White or Page Canvas: primary canvas and breathing room.
- 20% Royal Navy: headings, navigation and hierarchy.
- 7% Agent Blue and Cyan: actions and interaction.
- 3% Transform Violet: sparks and transformation moments.
- Violet should normally occupy less area than blue. It represents transformation and loses meaning when overused.

## 6. Accessibility rules

- Use Royal Navy or Slate for normal text on white and Page Canvas.
- White on Royal Navy has strong contrast and is suitable for navigation and compact dark sections.
- White on Transform Violet is suitable for normal text.
- White on Agent Blue is exactly at the normal-text threshold; reserve it for bold 18 px+ text or verify the final component independently.
- Use Royal Navy text on Intelligence Cyan.
- Never put text directly across the blue-violet gradient unless a solid accessible overlay is added.
- All interfaces must meet WCAG 2.2 AA: 4.5:1 for normal text, 3:1 for large text and interface boundaries.
- Keyboard focus uses a 2 px Intelligence Cyan ring plus a 2 px white offset on dark surfaces.
- The approved wordmark colour `#011141` is not a general-purpose background colour.

## 7. Typography

- Display and campaign headings: **Manrope**, weights 600–700.
- Product UI and body copy: **Inter**, weights 400–600.
- Fallback: `Arial, sans-serif`.
- Use sentence case. Avoid all-caps paragraphs and decorative fonts.

### Product type scale

| Style | Size / line height | Weight |
|---|---|---|
| Display | 56 / 64 px | 700 |
| H1 | 40 / 48 px | 700 |
| H2 | 32 / 40 px | 700 |
| H3 | 24 / 32 px | 600 |
| Body large | 18 / 28 px | 400 |
| Body | 16 / 24 px | 400 |
| Small | 14 / 20 px | 400 |
| Label | 14 / 20 px | 600 |

Marketing may scale headings proportionally, but must retain Manrope, the same weight hierarchy and readable line lengths.

## 8. Interface style

- Base spacing unit: 8 px. Use 4 px only for fine internal alignment.
- Standard radii: 8 px controls, 12 px cards, 16 px feature panels, pill only for tags.
- Primary button: Agent Blue background with white label; Button Blue on hover.
- Secondary button: white background, Agent Blue border and text.
- Links: Agent Blue; underline on hover and always within long-form text.
- Cards: white surface, 1 px Border outline; use shadows sparingly.
- Approved shadow: `0 8px 24px rgba(5, 7, 46, 0.10)`.
- Icons: rounded geometric line icons, 2 px stroke. Do not mix filled, hand-drawn and outline families.
- Photography: real people, real work and clean environments; use cool-neutral grading. Avoid generic robots, glowing brains and science-fiction clichés.

## 9. Logo rules

- Use only approved files from the Axyntro Violet-Spark Logo Package.
- Keep clear space equal to one endpoint shape on all sides.
- Minimum digital widths: symbol 24 px; horizontal lockup 140 px; stacked lockup 100 px.
- Use the colour logo on white or Page Canvas; use the white logo on Royal Navy, Footer Indigo or photography with sufficient contrast.
- Never recolour, redraw, stretch, rotate, crop, animate individual paths or separate the violet spark from the A.
- Do not recreate the wordmark by typing “Axyntro” in another font.

## 10. Marketing style

- Lead with one clear outcome, supported by one visual transformation story.
- Use Page Canvas, white and Royal Navy as the campaign foundation and reserve the saturated gradient for the transformation moment.
- Use the spark as a controlled motif: one prominent spark per composition, not a repeating decoration.
- Recommended tone: clear, confident, specific and human. Avoid exaggerated claims such as “fully autonomous,” “zero risk” or “instant transformation” unless demonstrably true.
- Working tagline: **From process to intelligent action.**

## 11. Governance and enforcement

### Single source of truth

Maintain one version-controlled `brand-system` repository containing approved logos, these design tokens, fonts, icon library, UI components and campaign templates. Teams must consume assets from the repository—not copy colours or logos from old presentations.

### Ownership

Appoint one Brand Owner and one Design-System Owner. The Brand Owner approves public-facing identity and campaigns. The Design-System Owner maintains tokens and coded components. Product, website and marketing teams may propose changes but cannot introduce new colours, fonts or logo treatments independently.

### Required controls

1. Lock colours, type styles and components into Figma libraries and restrict publishing rights.
2. Consume the supplied token names in code; prohibit raw hex values through linting except inside the token file.
3. Build websites and products from a shared component library with visual regression tests.
4. Provide approved presentation, document, social post, email and advertisement templates.
5. Add brand review and accessibility review as release gates for every public campaign and major interface release.
6. Run quarterly audits across live websites, applications, app stores, sales decks and active campaigns.
7. Record every approved exception with an owner, reason and expiry date.

### Definition of done

A branded asset is complete only when it:

- uses an approved logo file and clear space;
- uses only approved colour and typography tokens;
- passes WCAG 2.2 AA checks where applicable;
- uses approved components or templates;
- contains no unapproved stock-AI clichés or unsupported claims;
- has recorded approval from the responsible Brand Owner.

## 12. Review checklist

- [ ] Correct Axyntro violet-spark logo file and layout
- [ ] Correct background and minimum size
- [ ] No manually entered hex colours outside tokens
- [ ] Manrope and Inter used correctly
- [ ] Colour contrast checked
- [ ] Components taken from approved library
- [ ] Copy follows the Axyntro tone
- [ ] Legal, privacy and product claims reviewed where relevant
- [ ] Final Brand Owner approval recorded
