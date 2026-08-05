import { Router, type IRouter } from "express";
import { GetHealthResponse } from "@workspace/api-zod";

const router: IRouter = Router();

router.get("/healthz", (_req, res) => {
  const data = GetHealthResponse.parse({ status: "ok" });
  res.json(data);
});

export default router;
