import { readFileSync } from 'fs';
import { Script } from 'vm';

const html = readFileSync('templates/ozon/adaptation_workspace.html', 'utf-8');

// Extract all <script> content
const scriptRegex = /<script[^>]*>([\s\S]*?)<\/script>/gi;
let match;
const scripts = [];
while ((match = scriptRegex.exec(html)) !== null) {
  scripts.push(match[1]);
}

if (scripts.length === 0) {
  console.log('No scripts found in template');
  process.exit(0);
}

// Replace Jinja expressions with safe placeholders
let combined = scripts.join('\n');
combined = combined.replace(/\{\{[^}]*\}\}/g, '"JINJA_VAR"');
combined = combined.replace(/\{%[^%]*%\}/g, '/* JINJA_BLOCK */');
combined = combined.replace(/chr\(10\)/g, '"\\n"');
combined = combined.replace(/String\.fromCharCode\(10\)/g, '"\\n"');

try {
  new Script(combined);
  console.log('Template JavaScript syntax OK');
  process.exit(0);
} catch (e) {
  console.error('Template JavaScript syntax ERROR:');
  console.error(e.message);
  // Find line context
  if (e.stack) {
    const lines = combined.split('\n');
    const match = e.stack.match(/:(\d+)/);
    if (match) {
      const lineno = parseInt(match[1]) - 1;
      console.error(`  Line ${lineno + 1}: ${(lines[lineno] || '').substring(0, 100)}`);
    }
  }
  process.exit(1);
}
