import type { Components } from 'react-markdown';

/**
 * remark-gfm injects an h2#footnote-label with the literal "Footnotes", which duplicates
 * our own localized "## 证据来源 / ## Evidence footnotes" section heading. Drop that node.
 */
export const reportMarkdownComponents: Partial<Components> = {
  h2({ id, children, ...rest }) {
    if (id === 'footnote-label') {
      return null;
    }
    return (
      <h2 id={id} {...rest}>
        {children}
      </h2>
    );
  },
};
