import { Link } from "react-router-dom";
import { getArticle } from "./articles";
import styles from "./Help.module.css";

export function HelpLink({ to }: { to: string }) {
  const [category, slug] = to.split("/");
  const article = getArticle(category, slug);
  if (!article && import.meta.env.DEV) throw new Error(`Help article not found: ${to}`);
  const label = `Help: ${article?.title ?? "Article not found"}`;
  return <Link className={styles.helpLink} to={`/help/${to}`} aria-label={label} title={label}>?</Link>;
}
