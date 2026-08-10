import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import sharp from 'sharp';

const outputPath = resolve('docs/site/static/img/architecture/cubeplex-overview.svg');

const esc = (value) => value
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;');

const box = ({ x, y, w, h, kind = 'box', title, body = '', small = '', titleSize = 19 }) => {
  const cx = x + w / 2;
  const titleY = small ? y + 30 : body ? y + h / 2 - 5 : y + h / 2 + 6;
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="9" class="${kind}"/>
    <text x="${cx}" y="${titleY}" text-anchor="middle" class="name" style="font-size:${titleSize}px">${esc(title)}</text>
    ${body ? `<text x="${cx}" y="${titleY + 22}" text-anchor="middle" class="sub">${esc(body)}</text>` : ''}
    ${small ? `<text x="${cx}" y="${titleY + 43}" text-anchor="middle" class="tiny mono">${esc(small)}</text>` : ''}`;
};

const label = (x, y, text, anchor = 'middle') =>
  `<text x="${x}" y="${y}" text-anchor="${anchor}" class="label">${esc(text)}</text>`;

const svg = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1800 1170" role="img" aria-labelledby="title desc">
  <title id="title">CubePlex architecture overview</title>
  <desc id="desc">Web and IM clients connect to the CubePlex application and CubePi agent runtime. The runtime uses skills, memory, MCP tools, automations, artifacts, workspace sandboxes, model providers, MCP servers, and persistent infrastructure.</desc>
  <defs>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#27272a" stroke-width=".7"/></pattern>
    <marker id="arrow-blue" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0 0L9 3.5 0 7Z" fill="#6a83e3"/></marker>
    <marker id="arrow-gray" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0 0L9 3.5 0 7Z" fill="#71717a"/></marker>
    <style>
      text { font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
      .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
      .title { fill:#f4f4f5; font-size:34px; font-weight:700; letter-spacing:-.6px; }
      .subtitle { fill:#a1a1aa; font-size:17px; }
      .section { fill:#93a6ec; font-size:14px; font-weight:650; letter-spacing:.8px; }
      .panel { fill:#0a0a0b; stroke:#3f3f46; stroke-width:1.3; }
      .box { fill:#101014; stroke:#52525b; stroke-width:1.25; }
      .key { fill:#14213d; stroke:#6a83e3; stroke-width:1.7; }
      .sandbox { fill:#0c2f28; stroke:#34d399; stroke-width:1.7; }
      .infra { fill:#18181b; stroke:#52525b; stroke-width:1.2; }
      .name { fill:#f4f4f5; font-weight:650; }
      .sub { fill:#a1a1aa; font-size:13.5px; }
      .tiny { fill:#71717a; font-size:10.5px; }
      .label { fill:#93a6ec; font-size:12px; font-weight:600; }
      .line-blue { fill:none; stroke:#6a83e3; stroke-width:1.9; marker-end:url(#arrow-blue); stroke-linejoin:round; }
      .line-gray { fill:none; stroke:#71717a; stroke-width:1.45; marker-end:url(#arrow-gray); stroke-linejoin:round; }
      .line-dashed { fill:none; stroke:#6a83e3; stroke-width:1.45; stroke-dasharray:5 5; marker-end:url(#arrow-blue); stroke-linejoin:round; }
    </style>
  </defs>
  <rect width="1800" height="1170" fill="#18181b"/>
  <rect width="1800" height="1170" fill="url(#grid)"/>

  <text x="58" y="62" class="title">CubePlex architecture</text>
  <text x="58" y="92" class="subtitle">A cloud-native agent workspace: governed collaboration, agent execution, and isolated workspaces.</text>

  <rect x="48" y="230" width="370" height="258" rx="14" class="panel"/>
  <text x="76" y="262" class="section">TEAM ENTRY POINTS</text>
  ${box({ x: 76, y: 293, w: 314, h: 68, title: 'Web workspace', body: 'Next.js · chat · settings · artifacts' })}
  ${box({ x: 76, y: 390, w: 314, h: 68, title: 'IM bridges', body: 'Slack · Discord · Teams · Feishu · DingTalk' })}

  <rect x="472" y="132" width="842" height="705" rx="14" class="panel"/>
  <text x="502" y="164" class="section">CUBEPLEX APPLICATION</text>
  ${box({ x: 502, y: 194, w: 782, h: 76, kind: 'key', title: 'Organization, workspace, and access governance', body: 'memberships · roles · model policy · workspace-scoped APIs', titleSize: 20 })}
  ${box({ x: 502, y: 311, w: 782, h: 108, kind: 'key', title: 'FastAPI + CubePi agent runtime', body: 'streaming conversation runs · tool orchestration · approvals · checkpoints', small: 'SSE API · provider routing · policy enforcement', titleSize: 22 })}

  <text x="502" y="465" class="section">RUNTIME CAPABILITIES</text>
  ${box({ x: 502, y: 493, w: 182, h: 95, title: 'Skills', body: 'packaged workflows', small: 'built-in · uploaded · registry' })}
  ${box({ x: 702, y: 493, w: 182, h: 95, title: 'Memory', body: 'personal · workspace · org', small: 'pinned + relevance snapshots' })}
  ${box({ x: 902, y: 493, w: 182, h: 95, title: 'Automation + artifacts', body: 'schedules · webhooks · outputs', small: 'versioned files and previews', titleSize: 17 })}
  ${box({ x: 1102, y: 493, w: 182, h: 95, title: 'MCP catalog', body: 'tool discovery · OAuth', small: 'scoped credential grants' })}

  <rect x="502" y="643" width="782" height="160" rx="11" class="sandbox"/>
  <text x="526" y="673" class="section" fill="#6ee7b7">WORKSPACE SANDBOX</text>
  ${box({ x: 528, y: 698, w: 228, h: 75, kind: 'sandbox', title: 'Isolated runtime', body: 'shell · files · browser' })}
  ${box({ x: 779, y: 698, w: 228, h: 75, kind: 'sandbox', title: 'Persistent workspace', body: 'working tree · packages · files' })}
  ${box({ x: 1030, y: 698, w: 228, h: 75, kind: 'sandbox', title: 'Task-scoped access', body: 'network · env · command policy' })}

  <rect x="1368" y="132" width="384" height="705" rx="14" class="panel"/>
  <text x="1396" y="164" class="section">EXTERNAL SYSTEMS</text>
  ${box({ x: 1396, y: 195, w: 328, h: 68, title: 'IM platforms', body: 'message events and responses' })}
  ${box({ x: 1396, y: 318, w: 328, h: 68, title: 'Model providers', body: 'hosted and custom model APIs' })}
  ${box({ x: 1396, y: 441, w: 328, h: 68, title: 'Remote MCP servers', body: 'tools and connected services' })}
  ${box({ x: 1396, y: 643, w: 328, h: 160, kind: 'sandbox', title: 'OpenSandbox service', body: 'remote sandbox control plane', small: 'Docker or Kubernetes execution' })}

  <path d="M390 327H446V399H502" class="line-blue"/>
  <path d="M390 424H446V399H502" class="line-blue"/>
  ${label(446, 387, 'requests')}
  <path d="M893 270V311" class="line-blue"/>
  <path d="M593 419V493" class="line-gray"/>
  <path d="M793 419V493" class="line-gray"/>
  <path d="M993 419V493" class="line-gray"/>
  <path d="M1193 419V493" class="line-gray"/>
  <path d="M893 419V643" class="line-blue"/>
  ${label(910, 627, 'tool execution')}
  <path d="M1396 229H1328V384H1284" class="line-gray"/>
  ${label(1328, 372, 'events')}
  <path d="M1284 399H1360V352H1396" class="line-blue"/>
  ${label(1360, 339, 'model calls')}
  <path d="M1284 540H1330V475H1396" class="line-dashed"/>
  ${label(1307, 527, 'MCP tools')}
  <path d="M1284 723H1396" class="line-blue"/>
  ${label(1340, 710, 'sandbox API')}

  <rect x="472" y="890" width="1280" height="200" rx="14" class="panel"/>
  <text x="502" y="922" class="section">PERSISTENCE AND DEPLOYMENT</text>
  ${box({ x: 502, y: 954, w: 220, h: 96, kind: 'infra', title: 'PostgreSQL', body: 'organizations · conversations · state', small: 'CubePi checkpoints · governance' })}
  ${box({ x: 742, y: 954, w: 220, h: 96, kind: 'infra', title: 'Redis', body: 'coordination · caches · queues', small: 'OAuth and active-run state' })}
  ${box({ x: 982, y: 954, w: 220, h: 96, kind: 'infra', title: 'S3-compatible storage', body: 'attachments · artifacts · skills', small: 'RustFS in Docker Compose' })}
  ${box({ x: 1222, y: 954, w: 502, h: 96, kind: 'key', title: 'Deploy on Docker Compose or Kubernetes with Helm', body: 'the same frontend and backend images; optional OpenSandbox integration', titleSize: 17 })}
  <path d="M612 837V954" class="line-gray"/>
  <path d="M852 837V954" class="line-gray"/>
  <path d="M1092 837V954" class="line-gray"/>
</svg>`;

await mkdir(dirname(outputPath), { recursive: true });
const normalized = svg.trimStart().replace(/[ \t]+$/gm, '');
await writeFile(outputPath, normalized);
await sharp(Buffer.from(normalized)).resize({ width: 3600 }).png().toFile(
  outputPath.replace(/\.svg$/, '@2x.png'),
);

console.log(`Created ${outputPath}`);
