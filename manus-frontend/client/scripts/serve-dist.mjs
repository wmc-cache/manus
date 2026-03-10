#!/usr/bin/env node

import http from "node:http";
import https from "node:https";
import { createReadStream, existsSync, statSync } from "node:fs";
import { dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createBrotliCompress, createGzip, constants as zlibConstants } from "node:zlib";
import { pipeline } from "node:stream";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const distDir = resolve(__dirname, "..", "dist");
const port = Number(process.env.PORT || process.argv[2] || 3000);
const backendBaseUrl = new URL(
  process.env.API_BASE_URL || process.env.VITE_API_BASE_URL || "http://127.0.0.1:8000"
);
const proxyPrefixes = ["/api", "/proxy", "/ws"];

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webp": "image/webp",
};

const compressibleExtensions = new Set([
  ".css",
  ".html",
  ".js",
  ".json",
  ".map",
  ".svg",
  ".txt",
]);

function makeEtag(stats) {
  return `W/"${stats.size}-${Math.trunc(stats.mtimeMs).toString(16)}"`;
}

function resolveRequestTarget(requestUrl) {
  const pathname = decodeURIComponent(new URL(requestUrl, "http://localhost").pathname);
  const candidate = resolve(distDir, `.${pathname}`);

  if (!candidate.startsWith(distDir)) {
    throw new Error("invalid path");
  }

  if (existsSync(candidate) && statSync(candidate).isFile()) {
    return { filePath: candidate, pathname };
  }

  if (pathname.startsWith("/assets/")) {
    return null;
  }

  return {
    filePath: resolve(distDir, "index.html"),
    pathname: "/index.html",
  };
}

function getEncoding(acceptEncoding, extension) {
  if (!compressibleExtensions.has(extension)) {
    return null;
  }
  if (acceptEncoding.includes("br")) {
    return "br";
  }
  if (acceptEncoding.includes("gzip")) {
    return "gzip";
  }
  return null;
}

function shouldProxy(requestUrl) {
  const pathname = new URL(requestUrl, "http://localhost").pathname;
  return proxyPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function getBackendPath(requestUrl) {
  const incomingUrl = new URL(requestUrl, "http://localhost");
  const basePath = backendBaseUrl.pathname === "/" ? "" : backendBaseUrl.pathname.replace(/\/$/, "");
  return `${basePath}${incomingUrl.pathname}${incomingUrl.search}`;
}

function getBackendTransport() {
  return backendBaseUrl.protocol === "https:" ? https : http;
}

function getBackendPort() {
  if (backendBaseUrl.port) {
    return Number(backendBaseUrl.port);
  }
  return backendBaseUrl.protocol === "https:" ? 443 : 80;
}

function buildProxyHeaders(req) {
  const forwardedFor = req.socket.remoteAddress;
  return {
    ...req.headers,
    host: backendBaseUrl.host,
    "x-forwarded-for": forwardedFor ? forwardedFor : req.headers["x-forwarded-for"],
    "x-forwarded-host": req.headers.host,
    "x-forwarded-proto": req.socket.encrypted ? "https" : "http",
  };
}

function proxyHttpRequest(req, res) {
  const transport = getBackendTransport();
  const proxyReq = transport.request(
    {
      protocol: backendBaseUrl.protocol,
      hostname: backendBaseUrl.hostname,
      port: getBackendPort(),
      method: req.method,
      path: getBackendPath(req.url || "/"),
      headers: buildProxyHeaders(req),
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
      pipeline(proxyRes, res, () => {});
    }
  );

  proxyReq.on("error", (error) => {
    if (res.headersSent) {
      res.destroy(error);
      return;
    }
    res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`Bad Gateway: ${error.message}`);
  });

  req.on("aborted", () => {
    proxyReq.destroy();
  });

  pipeline(req, proxyReq, () => {});
}

function writeSocketResponseHead(socket, statusCode, statusMessage, headers) {
  const lines = [`HTTP/1.1 ${statusCode} ${statusMessage}`];

  for (const [key, value] of Object.entries(headers)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        lines.push(`${key}: ${item}`);
      }
      continue;
    }
    lines.push(`${key}: ${value}`);
  }

  socket.write(`${lines.join("\r\n")}\r\n\r\n`);
}

function closeSockets(socketA, socketB) {
  if (!socketA.destroyed) socketA.destroy();
  if (socketB && !socketB.destroyed) socketB.destroy();
}

function proxyUpgradeRequest(req, socket, head) {
  const transport = getBackendTransport();
  const proxyReq = transport.request({
    protocol: backendBaseUrl.protocol,
    hostname: backendBaseUrl.hostname,
    port: getBackendPort(),
    method: req.method || "GET",
    path: getBackendPath(req.url || "/"),
    headers: buildProxyHeaders(req),
  });

  proxyReq.on("upgrade", (proxyRes, proxySocket, proxyHead) => {
    writeSocketResponseHead(
      socket,
      proxyRes.statusCode || 101,
      proxyRes.statusMessage || "Switching Protocols",
      proxyRes.headers
    );

    if (proxyHead.length > 0) {
      socket.write(proxyHead);
    }
    if (head.length > 0) {
      proxySocket.write(head);
    }

    socket.on("error", () => closeSockets(socket, proxySocket));
    proxySocket.on("error", () => closeSockets(socket, proxySocket));

    proxySocket.pipe(socket);
    socket.pipe(proxySocket);
  });

  proxyReq.on("response", (proxyRes) => {
    writeSocketResponseHead(
      socket,
      proxyRes.statusCode || 502,
      proxyRes.statusMessage || "Bad Gateway",
      proxyRes.headers
    );
    proxyRes.pipe(socket);
  });

  proxyReq.on("error", (error) => {
    if (!socket.destroyed) {
      socket.write(
        `HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n${error.message}`
      );
    }
    closeSockets(socket);
  });

  proxyReq.end();
}

const server = http.createServer((req, res) => {
  if (!req.url) {
    res.writeHead(400).end("Bad Request");
    return;
  }

  if (shouldProxy(req.url)) {
    proxyHttpRequest(req, res);
    return;
  }

  if (req.method && req.method !== "GET" && req.method !== "HEAD") {
    res.writeHead(405, { Allow: "GET, HEAD" }).end("Method Not Allowed");
    return;
  }

  let target;
  try {
    target = resolveRequestTarget(req.url);
  } catch {
    res.writeHead(400).end("Bad Request");
    return;
  }

  if (!target) {
    res.writeHead(404).end("Not Found");
    return;
  }

  const { filePath, pathname } = target;
  const stats = statSync(filePath);
  const extension = extname(filePath);
  const etag = makeEtag(stats);
  const encoding = getEncoding(String(req.headers["accept-encoding"] || ""), extension);
  const headers = {
    "Cache-Control": pathname.startsWith("/assets/")
      ? "public, max-age=31536000, immutable"
      : "no-cache",
    "Content-Type": mimeTypes[extension] || "application/octet-stream",
    ETag: etag,
    "Last-Modified": stats.mtime.toUTCString(),
    Vary: "Accept-Encoding",
  };

  if (req.headers["if-none-match"] === etag) {
    res.writeHead(304, headers).end();
    return;
  }

  if (!encoding) {
    headers["Content-Length"] = stats.size;
  } else {
    headers["Content-Encoding"] = encoding;
  }

  res.writeHead(200, headers);

  if (req.method === "HEAD") {
    res.end();
    return;
  }

  const source = createReadStream(filePath);

  if (encoding === "br") {
    pipeline(
      source,
      createBrotliCompress({
        params: {
          [zlibConstants.BROTLI_PARAM_QUALITY]: 5,
        },
      }),
      res,
      () => {}
    );
    return;
  }

  if (encoding === "gzip") {
    pipeline(source, createGzip({ level: 6 }), res, () => {});
    return;
  }

  pipeline(source, res, () => {});
});

server.on("upgrade", (req, socket, head) => {
  if (!req.url || !shouldProxy(req.url)) {
    socket.write("HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }

  proxyUpgradeRequest(req, socket, head);
});

server.listen(port, "0.0.0.0", () => {
  console.log(`Serving ${distDir} on port ${port}, proxying API to ${backendBaseUrl.href}`);
});
