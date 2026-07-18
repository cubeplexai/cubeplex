import {readdir, readFile, writeFile} from 'node:fs/promises';
import {join, resolve} from 'node:path';

const buildDirectory = resolve(process.argv[2] ?? 'build');
const siteOrigin = 'https://cubeplex.ai';

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

function normalizeUrl(value) {
  const trimmed = value.trim();
  if (!trimmed || trimmed.startsWith('#')) {
    return value;
  }

  let url;
  let relative = false;
  try {
    if (trimmed.startsWith('/')) {
      url = new URL(trimmed, siteOrigin);
      relative = true;
    } else if (trimmed.startsWith(siteOrigin)) {
      url = new URL(trimmed);
    } else {
      return value;
    }
  } catch {
    return value;
  }

  if (url.origin !== siteOrigin || url.pathname === '/' || !url.pathname.endsWith('/')) {
    return value;
  }

  url.pathname = url.pathname.slice(0, -1);
  if (relative) {
    return `${url.pathname}${url.search}${url.hash}`;
  }
  return url.toString();
}

function normalizeHtml(content) {
  const attributePattern = /\b(?:href|src|content)=["']([^"']+)["']/gi;
  const normalizedAttributes = content.replace(attributePattern, (match, value) => {
    return match.replace(value, normalizeUrl(value));
  });

  const absoluteUrlPattern = /https?:\/\/[^\s"'<>]+/gi;
  return normalizedAttributes.replace(absoluteUrlPattern, (value) => {
    const url = value.replace(/[),.;]+$/, '');
    return normalizeUrl(url) + value.slice(url.length);
  });
}

function normalizeSitemap(content) {
  return content.replace(/<loc>([^<]+)<\/loc>/gi, (match, value) => {
    return match.replace(value, normalizeUrl(value));
  });
}

const files = await walk(buildDirectory);
let changedFiles = 0;
for (const file of files) {
  if (!file.endsWith('.html') && !file.endsWith('.xml')) {
    continue;
  }

  const original = await readFile(file, 'utf8');
  const normalized = file.endsWith('.html')
    ? normalizeHtml(original)
    : normalizeSitemap(original);
  if (normalized !== original) {
    await writeFile(file, normalized);
    changedFiles += 1;
  }
}

console.log(`Normalized trailing-slash URLs in ${changedFiles} generated files.`);
