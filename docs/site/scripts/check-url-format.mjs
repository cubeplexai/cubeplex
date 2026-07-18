import {readdir, readFile} from 'node:fs/promises';
import {join, relative, resolve} from 'node:path';

const buildDirectory = resolve(process.argv[2] ?? 'build');
const siteOrigin = 'https://cubeplex.ai';
const issues = [];

async function walk(directory) {
  const entries = await readdir(directory, {withFileTypes: true});
  const files = [];

  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...await walk(path));
    } else {
      files.push(path);
    }
  }

  return files;
}

function internalPath(value) {
  const trimmed = value.trim();
  if (
    !trimmed ||
    trimmed.startsWith('#') ||
    trimmed.startsWith('data:') ||
    trimmed.startsWith('mailto:') ||
    trimmed.startsWith('tel:') ||
    trimmed.startsWith('javascript:')
  ) {
    return null;
  }

  let url;
  try {
    if (trimmed.startsWith('//')) {
      url = new URL(`https:${trimmed}`);
    } else if (trimmed.startsWith('/')) {
      url = new URL(trimmed, siteOrigin);
    } else if (trimmed.startsWith(siteOrigin)) {
      url = new URL(trimmed);
    } else {
      return null;
    }
  } catch {
    return null;
  }

  return url.origin === siteOrigin ? url.pathname : null;
}

function checkValue(value, file, context) {
  const pathname = internalPath(value);
  if (pathname && pathname !== '/' && pathname.endsWith('/')) {
    issues.push(`${relative(process.cwd(), file)}: ${context} has ${value}`);
  }
}

function checkHtml(content, file) {
  const attributePattern = /\b(?:href|src|content)=["']([^"']+)["']/gi;
  for (const match of content.matchAll(attributePattern)) {
    checkValue(match[1], file, 'HTML URL');
  }

  const absoluteUrlPattern = /https?:\/\/[^\s"'<>]+/gi;
  for (const match of content.matchAll(absoluteUrlPattern)) {
    checkValue(match[0].replace(/[),.;]+$/, ''), file, 'HTML URL');
  }
}

function checkSitemap(content, file) {
  const locationPattern = /<loc>([^<]+)<\/loc>/gi;
  for (const match of content.matchAll(locationPattern)) {
    checkValue(match[1], file, 'sitemap URL');
  }
}

const files = await walk(buildDirectory);
for (const file of files) {
  const content = await readFile(file, 'utf8');
  if (file.endsWith('.html')) {
    checkHtml(content, file);
  } else if (file.endsWith('.xml')) {
    checkSitemap(content, file);
  }
}

if (issues.length > 0) {
  console.error('Found non-canonical trailing-slash URLs:');
  for (const issue of issues) {
    console.error(`- ${issue}`);
  }
  process.exitCode = 1;
} else {
  console.log(`URL format check passed for ${files.length} generated files.`);
}
