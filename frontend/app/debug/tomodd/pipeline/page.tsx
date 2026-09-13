import { redirect } from "next/navigation";

export default function RetiredTomoddDebugPage() {
  redirect("/public/tools/tomodd?notice=debug-retired");
}
