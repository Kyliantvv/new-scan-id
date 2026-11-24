import { describe, expect, it } from "vitest";

import { buildHeaders } from "./api";
import { describeFile, formatBytes, formatDate, viewFromUpload } from "./format";

describe("formatBytes", () => {
  it("affiche des unités françaises", () => {
    expect(formatBytes(512)).toBe("512 o");
    expect(formatBytes(2048)).toBe("2 Ko");
    expect(formatBytes(5 * 1024 * 1024 + 300_000)).toBe("5,3 Mo");
  });
});

describe("formatDate", () => {
  it("convertit l'ISO en JJ/MM/AAAA sans décalage de fuseau", () => {
    expect(formatDate("1974-08-12")).toBe("12/08/1974");
    expect(formatDate(null)).toBe("");
    expect(formatDate("pas une date")).toBe("");
  });
});

describe("describeFile", () => {
  it("suit l'envoi puis le job", () => {
    expect(describeFile({ kind: "uploading", fraction: 0.42 })).toMatchObject({ percent: 42 });
    const job = { id: "j", status: "running", progress: 0.5, stage: "dummy", error_code: null, attempts: 1 } as const;
    expect(describeFile({ kind: "job", job })).toMatchObject({ label: "En cours", percent: 50 });
    expect(describeFile({ kind: "job", job: { ...job, status: "done" } })).toMatchObject({
      percent: 100,
      tone: "success",
    });
  });

  it("explique les refus sans jargon", () => {
    const v = viewFromUpload({
      name: "x.gif",
      status: "rejected",
      file_id: null,
      job_id: null,
      duplicate_of_id: null,
      error_code: "UNSUPPORTED_FORMAT",
    });
    expect(describeFile(v).label).toContain("PDF, JPG ou PNG");
    expect(describeFile(v).tone).toBe("error");
  });
});

describe("buildHeaders", () => {
  it("envoie le jeton CSRF seulement sur les requêtes qui modifient", () => {
    expect(buildHeaders("POST", { a: 1 }, "jeton").get("X-CSRF-Token")).toBe("jeton");
    expect(buildHeaders("GET", undefined, "jeton").get("X-CSRF-Token")).toBeNull();
    expect(buildHeaders("POST", new FormData(), "jeton").get("Content-Type")).toBeNull();
  });
});
