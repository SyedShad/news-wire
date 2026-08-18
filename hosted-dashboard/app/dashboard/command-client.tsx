"use client";

import { useState } from "react";
import type { JsonValue, OwnerCommand } from "@/lib/dashboard/types.ts";

export type CommandPayload = Record<string, JsonValue>;

export function useOwnerCommand() {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function queue(operation: string, payload: CommandPayload, confirmation?: string): Promise<OwnerCommand | null> {
    if (confirmation && !window.confirm(confirmation)) return null;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/api/owner/commands", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ operation, payload }),
      });
      const result = await response.json() as { command?: OwnerCommand; error?: string };
      if (!response.ok || !result.command) throw new Error(result.error || "The command could not be queued");
      setMessage("Command accepted. Waiting for the laptop to confirm it…");
      const commandId = result.command.id;
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2_500));
        const statusResponse = await fetch("/api/owner/commands", { cache: "no-store" });
        if (!statusResponse.ok) continue;
        const statusResult = await statusResponse.json() as { commands?: OwnerCommand[] };
        const command = statusResult.commands?.find((item) => item.id === commandId);
        if (command?.status === "completed") {
          setMessage("Completed on the laptop. Synchronized information will refresh shortly.");
          break;
        }
        if (command?.status === "failed") {
          setMessage(command.error || "The laptop could not complete this command.");
          break;
        }
      }
      return result.command;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "The command could not be queued");
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { queue, message, busy };
}

export function CommandMessage({ message }: { message: string }) {
  return message ? <p className="owner-message" role="status">{message}</p> : null;
}
