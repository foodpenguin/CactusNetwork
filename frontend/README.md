# CactusNetwork Frontend

CactusNetwork is a Next.js frontend for an LLM-driven OTC and JIT liquidity settlement workflow.
The interface is designed for large-order trading scenarios, with wallet connection, trade submission, agent log visualization, and a dashboard for execution results.

## Tech Stack

- Next.js 16 App Router
- TypeScript
- Tailwind CSS v4
- wagmi
- TanStack Query
- Recharts

## Features

- Landing page for the CactusNetwork product story and positioning
- Trade page with buy and sell order forms
- Wallet connection through injected wallets such as MetaMask
- Simulated real-time agent terminal for execution flow visibility
- Dashboard with order summary cards, order table, and execution chart
- Static export support for GitHub Pages deployment

## Project Structure

```text
.
├── public/
├── src/
│   ├── app/
│   ├── components/
│   ├── hooks/
│   ├── lib/
│   └── types/
├── next.config.ts
├── package.json
└── tsconfig.json
```

## Getting Started

Install dependencies:

```bash
npm install
```

Start the development server:

```bash
npm run dev
```

Open the app at:

```text
http://localhost:3000
```

## Available Scripts

```bash
npm run dev
npm run build
npm run start
```

## Build Output

This project uses Next.js static export mode.
Running the build command generates a static site in the `out/` directory, which is used for GitHub Pages deployment.

```bash
npm run build
```

## Wallet Connection

The current wallet flow uses wagmi with the injected connector.
This means the app expects an injected wallet provider in the browser, such as MetaMask.

## API Integration

The frontend is structured to work with a backend that provides these routes:

- `POST /accounts`
- `POST /login`
- `POST /buy-orders`
- `POST /sell-orders`

Authentication is based on wallet address login, and the frontend stores the access token in memory for API requests.

## GitHub Pages Deployment

GitHub Pages deployment is handled by the workflow in `.github/workflows/deploy.yml`.
The workflow:

- installs dependencies with `npm ci`
- builds the project with `npm run build`
- uploads the `out/` directory
- deploys the static output to GitHub Pages

For project pages repositories, `next.config.ts` automatically applies the repository name as the base path during GitHub Actions builds.

## Notes

- The project root is this directory. There is no nested `frontend/` folder.
- The site icon and navbar logo both use the cactus brand asset.
- Build-time TypeScript validation for third-party vendor packages is currently skipped in Next.js build config to avoid external dependency typing failures from blocking deployment.