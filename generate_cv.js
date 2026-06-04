const { Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, BorderStyle, LevelFormat } = require('docx');
const fs = require('fs');

const data = JSON.parse(process.argv[2]);

function section(title) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [new TextRun(title)]
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    children: [new TextRun(text)]
  });
}

function bodyText(text, opts = {}) {
  return new Paragraph({
    spacing: { after: opts.after || 80 },
    children: [new TextRun({ text, ...opts })]
  });
}

function spacer() {
  return new Paragraph({ children: [new TextRun("")] });
}

const children = [];

// Header
children.push(new Paragraph({
  heading: HeadingLevel.HEADING_1,
  alignment: AlignmentType.CENTER,
  children: [new TextRun(data.name || "Your Name")]
}));

children.push(new Paragraph({
  alignment: AlignmentType.CENTER,
  spacing: { after: 300 },
  children: [new TextRun({ text: data.contact || "", size: 20, color: "555555" })]
}));

// Summary
if (data.summary) {
  children.push(section("Professional Summary"));
  children.push(bodyText(data.summary, { after: 200 }));
  children.push(spacer());
}

// Experience
if (data.experience && data.experience.length > 0) {
  children.push(section("Experience"));
  for (const job of data.experience) {
    children.push(bodyText(job.title + " – " + job.company, { bold: true, after: 40 }));
    children.push(bodyText(job.years, { color: "777777", italics: true, after: 100 }));
    if (job.bullets) {
      for (const b of job.bullets) children.push(bullet(b));
    }
    children.push(spacer());
  }
}

// Education
if (data.education && data.education.length > 0) {
  children.push(section("Education"));
  for (const edu of data.education) {
    children.push(bodyText(edu.degree + " – " + edu.institution, { bold: true, after: 40 }));
    children.push(bodyText(edu.years, { color: "777777", italics: true, after: 120 }));
  }
  children.push(spacer());
}

// Skills
if (data.skills && data.skills.length > 0) {
  children.push(section("Skills"));
  children.push(bodyText(data.skills.join("  •  "), { after: 120 }));
}

// Certifications
if (data.certifications && data.certifications.length > 0) {
  children.push(section("Certifications"));
  for (const c of data.certifications) children.push(bullet(c));
}

const doc = new Document({
  numbering: {
    config: [{
      reference: "bullets",
      levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 720, hanging: 360 } } } }]
    }]
  },
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 36, bold: true, font: "Arial", color: "1F3864" },
        paragraph: { spacing: { before: 0, after: 80 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, font: "Arial", color: "2E75B6" },
        paragraph: { spacing: { before: 240, after: 100 }, outlineLevel: 1,
          border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: "2E75B6", space: 1 } } } },
    ]
  },
  sections: [{
    properties: {
      page: { size: { width: 12240, height: 15840 }, margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 } }
    },
    children
  }]
});

Packer.toBuffer(doc).then(b => {
  const path = data.output || '/tmp/cv_output.docx';
  fs.writeFileSync(path, b);
  console.log('OK:' + path);
}).catch(e => { console.error('ERROR:' + e.message); process.exit(1); });
