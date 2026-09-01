import { Routes, Route } from "react-router-dom";
import Home from "./Home";
import Orders from "./Orders";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/orders" element={<Orders />} />
    </Routes>
  );
}
