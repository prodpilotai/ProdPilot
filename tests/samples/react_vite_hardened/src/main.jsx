import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";

function Orders() {
  return <h1>Orders</h1>;
}

function NotFound() {
  return <h1>Not found</h1>;
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Orders />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  </ErrorBoundary>
);
