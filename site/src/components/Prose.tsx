import type { ReactNode } from "react";
import { Layout } from "./Layout";

export function ProsePage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Layout>
      <article className="prose container">
        <h1 className="h1">{title}</h1>
        <p className="prose-notice" role="note">
          <strong>To be completed before launch.</strong> This page is a placeholder and is not yet a legal text.
        </p>
        {children}
      </article>
    </Layout>
  );
}
