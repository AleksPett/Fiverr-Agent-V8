const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, BorderStyle, LevelFormat } = require('docx');
const fs = require('fs');

const data = JSON.parse(process.argv[2]);

function section(title) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(title)] });
}
function body(text, opts = {}) {
  return new Paragraph({ spacing: { after: opts.after || 100 }, children: [new TextRun({ text, ...opts })] });
}
function bullet(text) {
  return new Paragraph({ numbering: { reference: "bullets", level: 0 }, children: [new TextRun(text)] });
}
function spacer() { return new Paragraph({ children: [new TextRun("")] }); }
function label(text) {
  return new Paragraph({ spacing: { after: 60 }, children: [new TextRun({ text, bold: true, color: "2E75B6", size: 20 })] });
}
function note(text) {
  return new Paragraph({ spacing: { after: 160 }, children: [new TextRun({ text, color: "777777", italics: true, size: 18 })] });
}

const children = [];

// Title
children.push(new Paragraph({
  heading: HeadingLevel.HEADING_1, alignment: AlignmentType.CENTER,
  children: [new TextRun("LinkedIn Profile")]
}));
children.push(body("Ready to copy-paste into your LinkedIn profile", { color: "777777", italics: true, after: 300, size: 20 }));

// Headline
if (data.headline) {
  children.push(section("Headline"));
  note("Copy this into the 'Headline' field on your profile (220 characters max)");
  children.push(body(data.headline, { bold: true, after: 200 }));
  children.push(spacer());
}

// About / Summary
if (data.about) {
  children.push(section("About"));
  note("Copy this into the 'About' section (2,600 characters max)");
  children.push(body(data.about, { after: 200 }));
  children.push(spacer());
}

// Experience sections
if (data.experience && data.experience.length > 0) {
  children.push(section("Experience Descriptions"));
  note("Copy each description into the corresponding role on LinkedIn");
  for (const job of data.experience) {
    children.push(body(job.title + " at " + job.company, { bold: true, after: 60 }));
    children.push(body(job.description, { after: 160 }));
    children.push(spacer());
  }
}

// Skills
if (data.skills && data.skills.length > 0) {
  children.push(section("Skills to Add"));
  note("Add these under 'Skills' on LinkedIn – prioritize the top 5");
  for (const s of data.skills) children.push(bullet(s));
  children.push(spacer());
}

// Featured / Bio quote
if (data.tagline) {
  children.push(section("Featured Section / Bio Quote"));
  note("Optional: use this in your Featured section or as a pinned post");
  children.push(body(data.tagline, { italics: true, after: 200 }));
}

const doc = new Document({
  numbering: {
    config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] }]
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: "0A66C2" },
        paragraph: { spacing: { before: 0, after: 100 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "1F3864" },
        paragraph: { spacing: { before: 240, after: 100 }, outlineLevel: 1,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "0A66C2", space: 1 } } } },
    ]
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } } },
    children
  }]
});

Packer.toBuffer(doc).then(b => {
  const path = data.output || '/tmp/linkedin_output.docx';
  fs.writeFileSync(path, b);
  console.log('OK:' + path);
}).catch(e => { console.error('ERROR:' + e.message); process.exit(1); });
