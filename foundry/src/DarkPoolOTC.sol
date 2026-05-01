// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title DarkPoolOTC
/// @notice Minimal dark pool for atomic peer-to-peer swaps.
contract DarkPoolOTC is Ownable {
    using SafeERC20 for IERC20;

    address public settlementRouter;

    event AtomicSwapExecuted(
        address indexed userA,
        address indexed userB,
        address tokenA,
        uint256 amountA,
        address tokenB,
        uint256 amountB
    );

    constructor() Ownable(msg.sender) {}

    function setSettlementRouter(address _router) external onlyOwner {
        require(_router != address(0), "Invalid router address");
        settlementRouter = _router;
    }

    modifier onlyRouter() {
        require(msg.sender == settlementRouter, "Unauthorized: only router");
        _;
    }

    /// @notice Execute atomic swap. Router must pre-transfer both tokens here.
    function executeAtomicSwap(
        address userA,
        address tokenA,
        uint256 amountA,
        address userB,
        address tokenB,
        uint256 amountB
    ) external onlyRouter {
        IERC20(tokenA).safeTransfer(userB, amountA);
        IERC20(tokenB).safeTransfer(userA, amountB);

        emit AtomicSwapExecuted(userA, userB, tokenA, amountA, tokenB, amountB);
    }
}
