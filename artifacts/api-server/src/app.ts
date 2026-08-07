import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
// Restrict CORS to Replit preview domains and localhost dev.
// This server only exposes health endpoints; no cross-origin writes are possible,
// but we still lock down to prevent the permissive default from being inherited
// if routes expand in future.
app.use(
  cors({
    origin: (origin, cb) => {
      if (!origin) return cb(null, true); // same-site / server-to-server
      const allowed =
        /^https:\/\/[a-z0-9-]+\.replit\.dev$/.test(origin) ||
        /^https?:\/\/localhost(:\d+)?$/.test(origin);
      cb(null, allowed);
    },
    credentials: true,
  }),
);
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

export default app;
