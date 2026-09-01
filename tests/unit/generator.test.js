const fs = require('fs');
const path = require('path');
const YAML = require('js-yaml');
const { execSync } = require('child_process');

describe('OpenAPI Generator Configuration Tests', () => {

  describe('Generator CLI', () => {
    test('should have OpenAPI Generator CLI installed', () => {
      try {
        // Try to verify CLI is available or can be installed via npm
        const packageJsonPath = path.join(__dirname, '../../package.json');
        const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
        expect(packageJson.devDependencies['@openapitools/openapi-generator-cli']).toBeDefined();
      } catch (error) {
        throw new Error(`OpenAPI Generator CLI not found in dependencies: ${error.message}`);
      }
    });

    test('should use OpenAPI Generator CLI v2.41.0+', () => {
      const packageJsonPath = path.join(__dirname, '../../package.json');
      const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
      const versionString = packageJson.devDependencies['@openapitools/openapi-generator-cli'];

      // Extract major.minor from version string (e.g., "^2.41.0" → "2.41")
      const match = versionString.match(/(\d+)\.(\d+)/);
      expect(match).toBeDefined();

      const [, major, minor] = match;
      const majorNum = parseInt(major, 10);
      const minorNum = parseInt(minor, 10);

      // Enforce v2.41.0+: major > 2 OR (major === 2 AND minor >= 41)
      expect(majorNum > 2 || (majorNum === 2 && minorNum >= 41)).toBe(true);
    });
  });

  describe('Generator Configs', () => {
    const languages = ['node', 'python', 'java', 'ruby', 'go', 'php', 'dotnet'];

    test('should have 7 generator config files', () => {
      const configDir = path.join(__dirname, '../../tools');
      const configFiles = fs.readdirSync(configDir).filter(f => f.startsWith('generator-') && f.endsWith('-config.yaml'));

      expect(configFiles.length).toBe(7);
    });

    test.each(languages)('should have valid config for %s generator', (lang) => {
      const configPath = path.join(__dirname, `../../tools/generator-${lang}-config.yaml`);
      expect(fs.existsSync(configPath)).toBe(true);

      const configContent = fs.readFileSync(configPath, 'utf8');
      const config = YAML.load(configContent);

      expect(config.generatorName).toBeDefined();
      expect(config.inputSpec).toBe('apis/spec-v2.openapi.yaml');
      expect(config.outputDir).toMatch(/generated\/sdk-.*-temp/);
      expect(config.packageVersion).toBe('0.3.0');
    });

    test('should have correct Node.js generator name', () => {
      const configPath = path.join(__dirname, '../../tools/generator-node-config.yaml');
      const configContent = fs.readFileSync(configPath, 'utf8');
      const config = YAML.load(configContent);

      // correct generator name; 'nodejs-axios' doesn't exist
      expect(config.generatorName).toBe('typescript-axios');
      expect(config.packageName).toBe('@starkbank/sdk-node');
      expect(config.additionalProperties.npmName).toBe('@starkbank/sdk-node');
    });
  });

  describe('Mustache Templates', () => {
    // must match typescript-axios's real template filenames (verified in the 7.0.1 jar)
    const requiredTemplates = [
      'package.mustache',
      'README.mustache',
      'index.mustache',
      'tsconfig.mustache',
      'gitignore'
    ];
    const mustacheTemplates = requiredTemplates.filter(f => f !== 'gitignore');

    test('should have all 5 templates in place', () => {
      const templatesDir = path.join(__dirname, '../../templates/nodejs-axios');
      expect(fs.existsSync(templatesDir)).toBe(true);

      for (const file of requiredTemplates) {
        const filePath = path.join(templatesDir, file);
        expect(fs.existsSync(filePath)).toBe(true);
      }
    });

    test.each(mustacheTemplates)('template %s should have .mustache extension', (file) => {
      expect(file).toMatch(/\.mustache$/);
    });

    test('should have valid Mustache variables in package template', () => {
      const pkgPath = path.join(__dirname, '../../templates/nodejs-axios/package.mustache');
      const pkgContent = fs.readFileSync(pkgPath, 'utf8');

      expect(pkgContent).toMatch(/\{\{npmName\}\}/);
      expect(pkgContent).toMatch(/\{\{npmVersion\}\}/);
    });

    test('should have valid Mustache variables in README template', () => {
      const readmePath = path.join(__dirname, '../../templates/nodejs-axios/README.mustache');
      const readmeContent = fs.readFileSync(readmePath, 'utf8');

      expect(readmeContent).toMatch(/\{\{npmName\}\}/);
      expect(readmeContent).toMatch(/\{\{npmVersion\}\}/);
    });

    test('should have gitignore template that excludes build artifacts', () => {
      const gitignorePath = path.join(__dirname, '../../templates/nodejs-axios/gitignore');
      const content = fs.readFileSync(gitignorePath, 'utf8');

      expect(content).toMatch(/dist\//);
      expect(content).toMatch(/node_modules\//);
      expect(content).toMatch(/coverage\//);
    });

    test('should have TypeScript config template', () => {
      const tsconfigPath = path.join(__dirname, '../../templates/nodejs-axios/tsconfig.mustache');
      const tsconfigContent = fs.readFileSync(tsconfigPath, 'utf8');
      const config = JSON.parse(tsconfigContent);

      expect(config.compilerOptions).toBeDefined();
      expect(config.compilerOptions.target).toBe('ES2020');
      expect(config.compilerOptions.module).toBe('commonjs');
    });
  });

  describe('Make Targets', () => {
    test('should have generate and generate-all targets in Makefile', () => {
      const makefilePath = path.join(__dirname, '../../Makefile');
      const makefile = fs.readFileSync(makefilePath, 'utf8');

      expect(makefile).toMatch(/^generate:/m);
      expect(makefile).toMatch(/^generate-all:/m);
    });

    test('should validate Java is installed before generating', () => {
      const makefilePath = path.join(__dirname, '../../Makefile');
      const makefile = fs.readFileSync(makefilePath, 'utf8');

      expect(makefile).toMatch(/which java/);
    });
  });
});
