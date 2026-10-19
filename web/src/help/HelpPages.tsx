import { useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { articles, categories, getArticle, relatedArticles, type HelpArticle } from "./articles";
import { readHelpFeedback, saveHelpFeedback, type HelpFeedback } from "./feedback";
import { renderMarkdown } from "./markdown";
import { searchArticles } from "./search";
import styles from "./Help.module.css";

function HelpSidebar({ currentArticle }: { currentArticle?: HelpArticle }) {
  return <aside className={styles.sidebar} aria-label="Help categories">
    {categories.map((category) => <section className={styles.sidebarCategory} key={category.id}>
      <h2>{category.title}</h2>
      <ul>{articles.filter((article) => article.category === category.id).map((article) => {
        const current = currentArticle !== undefined && article.category === currentArticle.category && article.slug === currentArticle.slug;
        return <li key={article.slug}><Link to={`/help/${article.category}/${article.slug}`} aria-current={current ? "page" : undefined}>{article.title}</Link></li>;
      })}</ul>
    </section>)}
  </aside>;
}

function HelpLayout({ children, currentArticle }: { children: ReactNode; currentArticle?: HelpArticle }) {
  return <main className={`${styles.helpPage} page`} id="main-content">
    <div className={styles.layout}>
      <HelpSidebar currentArticle={currentArticle} />
      <div className={styles.content}>{children}</div>
    </div>
  </main>;
}

export function HelpHomePage() {
  const [query, setQuery] = useState("");
  const results = useMemo(() => searchArticles(query, articles), [query]);
  const searching = query.trim().length > 0;
  return <HelpLayout>
    <h1>Help center</h1>
    <div className={`${styles.search} field`}>
      <label htmlFor="help-search">Search help articles</label>
      <input id="help-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    {searching ? results.length ? <section className={styles.results} aria-label="Search results">
      {results.map(({ article, snippet }) => <article className={styles.result} key={`${article.category}/${article.slug}`}>
        <h2><Link to={`/help/${article.category}/${article.slug}`}>{article.title}</Link></h2>
        <p className={styles.resultCategory}>{categories.find(({ id }) => id === article.category)?.title}</p>
        <p>{snippet}</p>
      </article>)}
    </section> : <p role="status">No articles match “{query.trim()}”.</p> : <div className={styles.categoryCards}>
      {categories.map((category) => <section className={`${styles.categoryCard} panel`} key={category.id}>
        <h2>{category.title}</h2>
        <ul>{articles.filter((article) => article.category === category.id).map((article) => <li key={article.slug}>
          <Link to={`/help/${article.category}/${article.slug}`}>{article.title}</Link>
          <p>{article.summary}</p>
        </li>)}</ul>
      </section>)}
    </div>}
  </HelpLayout>;
}

function handleMarkdownClick(event: MouseEvent<HTMLDivElement>, navigate: ReturnType<typeof useNavigate>) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  if (!(event.target instanceof Element)) return;
  const anchor = event.target.closest("a");
  if (!anchor || anchor.target || anchor.hasAttribute("download")) return;
  const url = new URL(anchor.href, window.location.href);
  if (url.origin !== window.location.origin) return;
  event.preventDefault();
  navigate(`${url.pathname}${url.search}${url.hash}`);
}

function Feedback({ article }: { article: HelpArticle }) {
  const path = `${article.category}/${article.slug}`;
  const [savedFeedback, setSavedFeedback] = useState<HelpFeedback | undefined>(() => readHelpFeedback()[path]);
  const submit = (helpful: HelpFeedback) => {
    saveHelpFeedback(path, helpful);
    setSavedFeedback(helpful);
  };
  return <section className={styles.feedback} aria-labelledby="helpful-heading">
    <h2 id="helpful-heading">Was this helpful?</h2>
    {savedFeedback ? <p role="status">Thanks for your feedback.</p> : <div className={styles.feedbackButtons}>
      <button type="button" onClick={() => submit("yes")}>Yes</button>
      <button type="button" onClick={() => submit("no")}>No</button>
    </div>}
  </section>;
}

export function HelpArticlePage() {
  const { category = "", article: slug = "" } = useParams();
  const navigate = useNavigate();
  const article = getArticle(category, slug);
  if (!article) return <HelpLayout>
    <h1>Article not found</h1>
    <p>The help article you requested could not be found.</p>
    <Link to="/help">Back to Help center</Link>
  </HelpLayout>;

  const categoryInfo = categories.find(({ id }) => id === article.category)!;
  const categoryArticles = articles.filter((candidate) => candidate.category === article.category);
  const articleIndex = categoryArticles.findIndex((candidate) => candidate.slug === article.slug);
  const previous = categoryArticles[articleIndex - 1];
  const next = categoryArticles[articleIndex + 1];
  const related = relatedArticles(article);
  return <HelpLayout currentArticle={article}>
    <nav className={styles.breadcrumb} aria-label="Breadcrumb">
      <Link to="/help">Help center</Link><span aria-hidden="true">›</span><span>{categoryInfo.title}</span>
    </nav>
    <article>
      <h1>{article.title}</h1>
      <p className={styles.summary}>{article.summary}</p>
      <div className={styles.markdown} onClick={(event) => handleMarkdownClick(event, navigate)} dangerouslySetInnerHTML={{ __html: renderMarkdown(article.body) }} />
    </article>
    <section className={styles.related} aria-labelledby="related-heading">
      <h2 id="related-heading">Related articles</h2>
      {related.length ? <ul>{related.map((item) => <li key={`${item.category}/${item.slug}`}><Link to={`/help/${item.category}/${item.slug}`}>{item.title}</Link></li>)}</ul> : <p>No related articles.</p>}
    </section>
    <Feedback article={article} />
    <nav className={styles.previousNext} aria-label="Articles in this category">
      {previous ? <Link to={`/help/${previous.category}/${previous.slug}`}>Previous: {previous.title}</Link> : <span />}
      {next ? <Link to={`/help/${next.category}/${next.slug}`}>Next: {next.title}</Link> : <span />}
    </nav>
  </HelpLayout>;
}
