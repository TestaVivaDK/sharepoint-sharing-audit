import { forwardRef } from 'react';
import { DataGrid, type DataGridProps } from '@mui/x-data-grid';
import { DataGridPro, type DataGridProProps } from '@mui/x-data-grid-pro';

/**
 * CustomDataGrid
 * * A wrapper that acts exactly like DataGridPro from an API perspective.
 * It automatically downgrades to the free DataGrid if no license key is present.
 */
const CustomDataGrid = forwardRef<HTMLDivElement, DataGridProProps>((props, ref) => {
  // 1. Verify the license key. 
  // (Update this env variable to match your build setup: Next.js, Vite, Webpack, etc.)
  const hasProLicense = Boolean(window.ENV?.MUI_LICENSE_KEY);

  // 2. Render Pro version if licensed
  if (hasProLicense) {
    return <DataGridPro ref={ref} {...props} />;
  }

  // 3. Fallback to Free version
  // We use `unknown` as an intermediate cast because DataGridProProps contains 
  // features (like specific apiRef typings or treeData) that strictly conflict 
  // with the free DataGridProps interface. 
  const freeProps = props as unknown as DataGridProps;

  // The free DataGrid component will safely ignore the Pro-only props at runtime.
  return <DataGrid ref={ref} {...freeProps} />;
});

// Setting a displayName is a React best practice when using forwardRef,
// ensuring the component shows up clearly in React DevTools.
CustomDataGrid.displayName = 'CustomDataGrid';

export default CustomDataGrid;