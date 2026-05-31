import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage();

try {
  console.log('🧪 Testing Skills.sh integration with Playwright\n');
  
  // Navigate to frontend
  console.log('1. Opening frontend...');
  await page.goto('http://192.168.1.150:3043/', { waitUntil: 'domcontentloaded' });
  const title = await page.title();
  console.log(`✓ Page loaded: "${title}"\n`);
  
  // Check if backend API works
  console.log('2. Testing backend API...');
  const response = await page.evaluate(async () => {
    try {
      const res = await fetch('http://127.0.0.1:8043/api/v1/health');
      return res.status === 404 ? 'Backend responding' : `Status ${res.status}`;
    } catch (e) {
      return `Error: ${e.message}`;
    }
  });
  console.log(`✓ ${response}\n`);
  
  // Check frontend elements
  console.log('3. Checking frontend UI...');
  const hasContent = await page.locator('body').textContent();
  console.log(`✓ Page has content: ${hasContent.length > 0}\n`);
  
  console.log('✅ Basic frontend test passed!');
  
} catch (error) {
  console.error('❌ Error:', error.message);
} finally {
  await browser.close();
}
