# amazev.ai Documentation

This directory contains the documentation for amazee.ai, configured to be displayed at [docs.amazee.ai](https://docs.amazee.ai).

## Structure

```
docs/
├── web/                    # Main documentation files
│   ├── index.md           # Home page
│   ├── installation.md    # Installation guide
│   ├── configuration.md   # Configuration guide
│   ├── user-guide.md      # User guide
│   ├── api-reference.md   # API reference
│   ├── deployment.md      # Deployment guide
│   └── troubleshooting.md # Troubleshooting guide
└── README.md              # This file
```

## Building Locally

### Prerequisites

- Python 3.8+
- pip

### Installation

1. Install dependencies:
   ```bash
   pip install -r docs-requirements.txt
   ```

2. Build the documentation:
   ```bash
   mkdocs build
   ```

3. Serve locally:
   ```bash
   mkdocs serve
   ```

   The documentation will be available at http://127.0.0.1:8000

### Using the Build Script

For convenience, you can use the build script:

```bash
./build-docs.sh
```

## Development

### Adding New Pages

1. Create a new markdown file in `docs/web/`
2. Add it to the navigation in `mkdocs.yml`
3. Update the table of contents if needed

### Styling

The documentation uses the Material for MkDocs theme with custom styling. To add custom CSS:

1. Create `docs/stylesheets/extra.css`
2. Add your custom styles
3. The file will be automatically included

### Configuration

The main configuration is in `mkdocs.yml` at the root of the repository. Key settings:

- **Site URL**: https://docs.amazee.ai
- **Theme**: Material for MkDocs
- **Navigation**: Configured in the `nav` section
- **Plugins**: Search, git revision dates, minification

## Deployment

### Automatic Deployment

The documentation is automatically deployed to GitHub Pages when changes are pushed to the `main` branch. The deployment is handled by the GitHub Actions workflow in `.github/workflows/docs.yml`.

### Manual Deployment

To deploy manually:

```bash
mkdocs gh-deploy
```

## Contributing

1. Make changes to the markdown files in `docs/web/`
2. Test locally with `mkdocs serve`
3. Commit and push your changes
4. The documentation will be automatically deployed

## Features

- **Search**: Full-text search across all documentation
- **Dark Mode**: Toggle between light and dark themes
- **Mobile Responsive**: Optimized for all device sizes
- **Code Highlighting**: Syntax highlighting for code blocks
- **Version Control**: Git revision dates and edit links
- **SEO Optimized**: Meta tags and structured data